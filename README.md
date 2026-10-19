# Knowledge Lab v2

Оюутны бодлого бодсон түүхийг (interaction log) судалгаанд зориулж цуглуулах Django + PostgreSQL систем. Энэ лог нь динамик мэдлэгийн граф, адаптив тархалтын загварт оролт болно.

## Асаах (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-db.ps1   # PostgreSQL 17, 127.0.0.1:5433, UTC
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe -m pytest                             # тусдаа test_knowledge_lab DB ашиглана
```

Анх удаа суулгахдаа: `py -3.12 -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt`, `.env.example`-ийг `.env` болгон хуулж утгуудыг бөглөнө.

## Өгөгдлийн зарчим

- **Нэг мөр = нэг submit.** `events.SubmissionEvent` хүснэгтэд зөвхөн шинэ мөр нэмнэ. UPDATE, DELETE-ийг DB trigger хориглоно. Дүнгийн засвар `GradeRevision` хүснэгтэд бичигдэнэ.
- **Нууцлал.** Лог-д зөвхөн `HMAC(PSEUDONYM_KEY, user_id)` pseudonym хадгалагдана. Хариултын текст export-д ордоггүй тусдаа хүснэгтэд (`SubmissionAnswer`) байна.
- **Хувилбар.** Нийтлэгдсэн `QuestionVersion` болон түүний concept жинг өөрчлөх боломжгүй. Засах бол шинэ хувилбар үүсгэнэ. Event бүр өөрийн хувилбар болон concept-ийн snapshot-ийг хадгална.
- **Concept жин.** Нэг хувилбарын жингийн нийлбэр 1 байх ёстой. Үүнийг commit үед шалгана. Concept заавал бодлоготой ижил курсэд байна.
- **Хугацаа.** Бүх хугацаа `timestamptz` төрлөөр, UTC-д. Export-д `...Z` хэлбэрээр гарна.
- **Hint.** Серверт тоологдоно (`HintView`). 0 гэдэг нь "hint үзээгүй" гэсэн утга.
- **Зөвшөөрөл.** Зөвшөөрөл өгөөгүй эсвэл буцаан татсан оюутан, мөн synthetic курсын event export-д орохгүй.

## Оюутны интерфейс ба демо

```powershell
.venv\Scripts\python.exe manage.py seed_demo      # synthetic курс + demo-student (.local/demo-credentials.txt)
.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Оюутны урсгал: нэвтрэх → зөвшөөрлийн сонголт (`ConsentLog`-д бичигдэнэ) → бодлого → hint → илгээх → өөрийн оролдлогын түүх. Маягт бүр нэг удаагийн `idempotency_key` агуулна. Тиймээс ижил маягтыг давхар илгээхэд нэг event л үүснэ.

## Багшийн хэсэг (`/admin/`)

```powershell
.venv\Scripts\python.exe manage.py setup_roles --teacher <username>        # «Багш» бүлэг + staff эрх
.venv\Scripts\python.exe manage.py import_students <course> students.csv  # CSV: username баганатай
```

Багштай баталсан concept графыг (`docs/graph_approval_form.docx`) CSV-ээр оруулна. Загвар болон зааврыг `docs/graph_templates/`-аас үзнэ үү:

```powershell
.venv\Scripts\python.exe manage.py import_graph <course> concepts.csv edges.csv --dry-run   # шалгах
.venv\Scripts\python.exe manage.py import_graph <course> concepts.csv edges.csv             # оруулах
```

`import_students` нь шинэ бүртгэл бүрт нууц үг үүсгэж `.local/credentials-*.csv` файлд бичнэ. Хэрэглэгчийн нэрэнд жинхэнэ нэр биш, оюутны код ашиглана.

| Ажил | Хаана |
|---|---|
| Concept, prerequisite холбоос, батлах | Concept-ууд, concept холбоосууд. Prerequisite цикл үүсгэх холбоосыг хүлээж авахгүй |
| Графыг зургаар харах, урьдчилсан бүртгэлийн нөхцөл шалгах | Хичээлүүд → «Граф харах» |
| Бодлого → ноорог хувилбар → concept жин (нийлбэр 1) → «Нийтлэх» | Бодлогууд, бодлогын хувилбарууд |
| Нийтлэгдсэн хувилбарыг засах | «Шинэ ноорог болгон хуулах» → засах → нийтлэх. Хуучин хувилбар RETIRED болно |
| Оролдлого үнэлэх | Оролдлогууд → «Үнэлэх шаардлагатай» шүүлтүүр → дүнгийн засвар нэмэх |
| Судалгааны CSV | Хичээлүүд → сонгох → «Судалгааны CSV татах» |

Багш оюутны зөвшөөрлийг өөрчлөх, event засах/устгах, хэрэглэгч засах эрхгүй.

## CODE бодлогын автомат үнэлгээ (Docker sandbox)

Шаардлага: Docker Desktop (WSL2-тэй) ажиллаж байх.

```powershell
.venv\Scripts\python.exe manage.py check_code_runner --build-c   # C image-ийг бүтээнэ (нэг удаа, интернэт хэрэгтэй)
.venv\Scripts\python.exe manage.py check_code_runner   # image татаж, sandbox-ийн хязгаарыг шалгана
.venv\Scripts\python.exe manage.py run_code_grader     # worker: PENDING CODE оролдлогуудыг үнэлнэ (--once: нэг удаа)
```

- **Хэл:** бодлогын хувилбар бүрт Python эсвэл C сонгоно (нийтэлсний дараа өөрчлөгдөхгүй). C кодыг container дотор `gcc -std=c11 -O2 -Wall` хөрвүүлж, дараа нь тест бүрээр ажиллуулна. Хөрвүүлэлтийн алдаа гарвал бүх тест унаж, оюутанд gcc-ийн мэдэгдэл (эхний 12 мөр) харагдана. C image: `docker/runner-c/Dockerfile` (python:3.12-slim + gcc).
- Оюутны код container дотор ажиллана: сүлжээгүй, root файлын систем зөвхөн уншигдана, санах ой 256 MB, процесс ≤ 64, эрхгүй `nobody` хэрэглэгч. Host-оос юу ч mount хийхгүй. Тест бүр 2 секундийн хязгаартай.
- Багш бодлогын хувилбарт тест (stdin → хүлээгдсэн stdout) болон шаардлагатай бол **driver код** оруулна. Driver оюутны кодын араас залгагдана (жишээ нь `print(total(json.loads(sys.stdin.read())))`).
- Харьцуулахдаа мөрийн төгсгөлийн хоосон зай болон сүүлийн хоосон мөрүүдийг үл тооно.
- Үр дүн `GradeRevision(source="code_runner")`-д бичигдэнэ. Тест бүрийн төлөв, хугацаа `CodeRun`-д хадгалагдана. Docker ажиллахгүй үед дүн бичигдэхгүй, алдааг бүртгээд дараа дахин оролдоно.
- Багш гараар үнэлсэн эсвэл тестгүй хувилбарын оролдлогыг worker алгасна.
- Оюутанд ил тестийн оролт, хүлээгдсэн гаралт, өөрийн гаралт харагдана. Нууц тестийн хувьд зөвхөн давсангүй тоо харагдана.

## Судалгааны engine

Django-оос тусдаа `engine/` package: replay, хугацааны/оюутны хуваалт, baseline (global, concept rate, PFA, BKT), DKG-AP загвар (decay, адаптив η ба α, нэг hop тархалт, fixed/learned/dynamic W), AUC/LogLoss/Brier/ECE, оюутны bootstrap CI, ablation. Дэлгэрэнгүйг [docs/engine.md](docs/engine.md)-ээс үзнэ үү.

```powershell
.venv\Scripts\python.exe manage.py export_graph <course> --out exports\graph.json
.venv\Scripts\python.exe -m engine run experiments\synthetic.json --out results\synthetic
```

## Export

```powershell
.venv\Scripts\python.exe manage.py export_events <course-code> --out exports\events.csv
```

Мөрүүд `(timestamp, event_id)`-аар эрэмбэлэгдэнэ. Багана бүрийн тайлбар [docs/data-dictionary.md](docs/data-dictionary.md)-д бий.
