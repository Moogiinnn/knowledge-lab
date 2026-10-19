# Knowledge Lab-ийг AWS Lightsail дээр суулгах заавар

Сервер: AWS Lightsail, Seoul, Ubuntu 24.04 LTS, 4 GB RAM / 2 vCPU / 80 GB SSD ($24/сар).
Нэг сервер дээр бүгд ажиллана: nginx → gunicorn (Django) → PostgreSQL 17, код үнэлэгч → Docker.

```
Оюутан/багш ──HTTPS──► nginx ──► gunicorn (Django) ──► PostgreSQL 17
                                     │
                     run_code_grader ┴──► Docker (Python, C, C++ sandbox)
```

> **Бодит оюутны өгөгдлийг HTTPS асаагүй байхад бүү цуглуул** (6-р алхам). Түүнээс өмнө зөвхөн демо/туршилтын өгөгдөл.

---

## 1. Сервер үүсгэх (AWS консол)

1. Lightsail → **Create instance** → Seoul (ap-northeast-2a) → Linux/Unix → **OS only → Ubuntu 24.04 LTS**.
2. Plan: **$24** (4 GB). Нэр: `knowledge-lab`. **Create instance**.
3. Instance → **Networking** → **Attach static IP** (сервер ажиллаж байхад үнэгүй).
4. **IPv4 Firewall**: SSH (22), HTTP (80), **HTTPS (443)** нээлттэй байх ёстой. 443-ийг **Add rule**-ээр нэмнэ.

## 2. Кодыг сервер рүү татах (private repository)

Lightsail дээр instance-ийн **Connect using SSH** (хөтөч дээр терминал нээгдэнэ) дарж доорх командуудыг ажиллуулна.

Сервер дээр зөвхөн уншдаг **deploy key** үүсгэнэ:

```bash
sudo mkdir -p /root/.ssh && sudo ssh-keygen -t ed25519 -N "" -C "knowledge-lab-server" -f /root/.ssh/github_deploy
sudo cat /root/.ssh/github_deploy.pub
```

Гарсан `ssh-ed25519 ...` мөрийг хуулаад GitHub → **Moogiinnn/knowledge-lab → Settings → Deploy keys → Add deploy key** хэсэгт оруулна (Title: `lightsail`, **Allow write access-ийг чеклэхгүй**).

```bash
printf 'Host github.com\n  IdentityFile /root/.ssh/github_deploy\n  IdentitiesOnly yes\n' | sudo tee -a /root/.ssh/config
sudo ssh -o StrictHostKeyChecking=accept-new -T git@github.com    # "successfully authenticated" гэж гарна
sudo git clone git@github.com:Moogiinnn/knowledge-lab.git /srv/knowledge-lab
```

## 3. Суулгах (нэг удаа, 10–15 минут)

`HOST`-д домэйн нэрийг (жишээ нь `kl.num.edu.mn`) бичнэ. Домэйн хараахан байхгүй бол туршилтад **static IP**-г бичиж болно.

```bash
sudo HOST=kl.num.edu.mn bash /srv/knowledge-lab/deploy/setup.sh
```

Скрипт: багцууд (nginx, PostgreSQL 17, Docker, certbot) → `klab` хэрэглэгч → өгөгдлийн сан ба `.env` (шинэ нууц түлхүүрүүдтэй) → Python орчин, migrate → Python/C/C++ sandbox image → вэб ба үнэлэгч үйлчилгээ → nginx → өдөр тутмын нөөцлөлт.

## 4. Админ (багш) данс үүсгэх

```bash
sudo -u klab bash -c "cd /srv/knowledge-lab && .venv/bin/python manage.py createsuperuser"
```

Багшийн эрх өгөх: `sudo -u klab bash -c "cd /srv/knowledge-lab && .venv/bin/python manage.py setup_roles --teacher <нэр>"`.

## 5. Шалгах

- Хөтөч дээр `http://<HOST>` → нэвтрэх хуудас гарна. `/admin/` → багшийн хэсэг.
- Код үнэлэгч: `sudo systemctl status knowledge-lab-grader` → `active (running)`.
- Sandbox: `sudo -u klab -g docker bash -c "cd /srv/knowledge-lab && .venv/bin/python manage.py check_code_runner"` → 15 шалгалт OK.

## 6. Домэйн ба HTTPS (бодит өгөгдлөөс өмнө заавал)

1. DNS: домэйны **A бичлэг**-ийг серверийн static IP руу заана (МУИС-ийн IT хэлтэс эсвэл домэйн бүртгэгч).
2. HTTPS сертификат (үнэгүй, Let's Encrypt; автоматаар сунгагдана):

```bash
sudo certbot --nginx -d kl.num.edu.mn --redirect -m <и-мэйл> --agree-tos -n
sudo sed -i 's/^DJANGO_HTTPS=false/DJANGO_HTTPS=true/' /srv/knowledge-lab/.env
sudo systemctl restart knowledge-lab-web
```

Хэрэв эхлээд IP-ээр суулгасан бол `.env`-ийн `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`-ийг домэйнээр сольж, `sudo HOST=<домэйн> bash deploy/setup.sh`-ийг дахин ажиллуулна (`.env` ба өгөгдөл хэвээр үлдэнэ).

## 7. Нөөцлөлт

- **Өдөр бүр 02:30-д** (Улаанбаатар) өгөгдлийн сан `/var/backups/knowledge-lab/`-д хадгалагдана, 14 хоног.
- **Lightsail snapshot**: Instance → **Snapshots → Enable automatic snapshots** (дискийн бүтэн хуулбар, ≈ $0.05/GB/сар).
- **S3 руу хуулах** (заавал биш): S3 bucket үүсгээд, зөвхөн тэр bucket-д бичих эрхтэй IAM хэрэглэгчийн түлхүүрээр `sudo apt install -y awscli && sudo aws configure` хийж, `echo 'S3_BUCKET=<bucket нэр>' | sudo tee /etc/default/knowledge-lab-backup`.
- Гараар нөөцлөх: `sudo knowledge-lab-backup`.

## 8. ⚠ PSEUDONYM_KEY-г хадгалах

`sudo grep PSEUDONYM_KEY /srv/knowledge-lab/.env` — энэ утгыг серверийн гадна, аюулгүй газар (нууц үгийн менежер эсвэл цаасан дээр, сейфэнд) хадгална. Алдагдвал оюутнуудын нууц нэр өөрчлөгдөж, өгөгдлийг улирал хооронд холбох боломжгүй болно. GitHub, и-мэйлд бүү хадгал.

## 9. Шинэчлэх

Компьютер дээрээ `git push` хийсний дараа сервер дээр:

```bash
sudo bash /srv/knowledge-lab/deploy/update.sh
```

(Эхлээд нөөцлөөд, кодыг татаж, migrate хийгээд, үйлчилгээг дахин асаана.)

## 10. Алдаа гарвал

| Шинж тэмдэг | Шалгах |
|---|---|
| 502 Bad Gateway | `sudo journalctl -u knowledge-lab-web -n 50` |
| Код үнэлэгдэхгүй «хүлээгдэж байна» | `sudo journalctl -u knowledge-lab-grader -n 50`, `sudo systemctl status docker` |
| 400 Bad Request | `.env`-ийн `DJANGO_ALLOWED_HOSTS` хөтчийн хаягтай таарч байгаа эсэх |
| CSRF алдаа (403) | `DJANGO_CSRF_TRUSTED_ORIGINS`-д `https://<домэйн>` байгаа эсэх |
