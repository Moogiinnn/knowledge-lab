import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.grading.runner import C_DOCKERFILE, Case, DockerRunner, InfrastructureError, docker_available, image_present

PYTHON_CHECKS = [
    ("prints output", "print(input())", [Case(1, "сайн уу")], lambda r: r[0].stdout.strip() == "сайн уу"),
    ("timeout enforced", "while True: pass", [Case(1, "")], lambda r: r[0].status == "timeout"),
    ("network blocked", "import socket; socket.create_connection(('1.1.1.1', 53), 3)", [Case(1, "")],
     lambda r: r[0].status == "runtime_error"),
    ("root fs read-only", "open('/home/x', 'w')", [Case(1, "")], lambda r: r[0].status == "runtime_error"),
    ("runs unprivileged", "import os; print(os.getuid())", [Case(1, "")], lambda r: r[0].stdout.strip() == "65534"),
]  # fmt: skip

C_NETWORK = """#include <arpa/inet.h>
#include <sys/socket.h>
int main(void) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = {0};
    a.sin_family = AF_INET; a.sin_port = htons(53); inet_pton(AF_INET, "1.1.1.1", &a.sin_addr);
    return connect(s, (struct sockaddr *)&a, sizeof a) == 0 ? 0 : 1;
}"""
C_CHECKS = [
    ("C compiles and prints", '#include <stdio.h>\nint main(void){char b[64]; if(scanf("%63s", b)==1) printf("%s\\n", b); return 0;}',
     [Case(1, "salam")], lambda r: r[0].stdout.strip() == "salam"),
    ("C compile error reported", "int main(void){ return x; }", [Case(1, "")],
     lambda r: r[0].status == "compile_error" and "solution.c" in r[0].stderr),
    ("C timeout enforced", "int main(void){ for(;;); }", [Case(1, "")], lambda r: r[0].status == "timeout"),
    ("C network blocked", C_NETWORK, [Case(1, "")], lambda r: r[0].status == "runtime_error"),
    ("C root fs read-only", '#include <stdio.h>\nint main(void){ return fopen("/home/x", "w") ? 0 : 1; }', [Case(1, "")],
     lambda r: r[0].status == "runtime_error"),
    ("C runs unprivileged", '#include <stdio.h>\n#include <unistd.h>\nint main(void){ printf("%d\\n", (int)getuid()); return 0; }',
     [Case(1, "")], lambda r: r[0].stdout.strip() == "65534"),
]  # fmt: skip

CPP_CHECKS = [
    ("C++ compiles and prints (STL)",
     "#include <bits/stdc++.h>\nusing namespace std;\nint main(){ vector<int> v{3,1,2}; sort(v.begin(), v.end());"
     " for(int x: v) cout << x; cout << '\\n'; return 0; }",
     [Case(1, "")], lambda r: r[0].stdout.strip() == "123"),
    ("C++ compile error reported", "int main(){ return x; }", [Case(1, "")],
     lambda r: r[0].status == "compile_error" and "solution.cpp" in r[0].stderr),
    ("C++ timeout enforced", "int main(){ for(;;); }", [Case(1, "")], lambda r: r[0].status == "timeout"),
    ("C++ runs unprivileged", "#include <iostream>\n#include <unistd.h>\nint main(){ std::cout << getuid() << '\\n'; }",
     [Case(1, "")], lambda r: r[0].stdout.strip() == "65534"),
]  # fmt: skip


class Command(BaseCommand):
    help = "Check that Docker works and the sandbox enforces its limits (pulls the Python image on first use)."

    def add_arguments(self, parser):
        parser.add_argument("--build-c", action="store_true",
                            help="build the C/C++ image (python:3.12-slim + gcc + g++) from docker/runner-c; needs network once")  # fmt: skip

    def handle(self, build_c, **options):
        runner = DockerRunner()
        c = runner.config
        if not docker_available(c):
            raise CommandError("Docker is not running. Start Docker Desktop (requires WSL2).")
        if build_c:
            context = Path(settings.BASE_DIR) / C_DOCKERFILE
            self.stdout.write(f"building {c.c_image} from {context} ...")
            if subprocess.run([c.docker, "build", "-t", c.c_image, str(context)]).returncode != 0:
                raise CommandError("docker build failed")

        failed = self.run_checks(runner, PYTHON_CHECKS, "PYTHON")
        if image_present(c.c_image, c):
            failed += self.run_checks(runner, C_CHECKS, "C")
            failed += self.run_checks(runner, CPP_CHECKS, "CPP")
        else:
            self.stdout.write(self.style.WARNING(
                f"SKIP C/C++ checks: image {c.c_image} not found. Run: manage.py check_code_runner --build-c"))  # fmt: skip
        if failed:
            raise CommandError(f"{failed} sandbox checks failed")
        self.stdout.write(f"Sandbox ready ({c.tag_for('PYTHON')}" + (f", {c.tag_for('C')}, C++)" if image_present(c.c_image, c) else ")"))

    def run_checks(self, runner, checks, language) -> int:
        failed = 0
        for label, code, cases, ok in checks:
            try:
                result = runner.run(code, "", cases, language=language)
            except InfrastructureError as exc:
                raise CommandError(str(exc)) from exc
            good = ok(result)
            failed += not good
            self.stdout.write(f"{'OK  ' if good else 'FAIL'} {label}: {result[0].status}")
        return failed
