# Граф импортлох загвар файлууд

`graph_approval_form.docx`-ийг багштай бөглөсний дараа үр дүнг эдгээр хоёр файлд шилжүүлээд `import_graph`-аар системд оруулна. Excel-ээр нээж засаад **CSV UTF-8** хэлбэрээр хадгална.

## concepts.csv
| Багана | Утга |
|---|---|
| `code` | Латин, жижиг үсэг, зураас (`linked-list`). Edge-д энэ кодоор холбогдоно |
| `name` | Харагдах нэр |
| `kind` | `Б` / `А` / `Ө`, эсвэл `STRUCTURE` / `ALGORITHM` / `OTHER`. H2-т заавал хэрэгтэй |
| `description` | Заавал биш |

Хичээлд ороогүй concept-ийн мөрийг устгана. Шинэ concept-ийг мөр нэмж оруулна.

## edges.csv
| Багана | Утга |
|---|---|
| `source`, `target` | concepts.csv-ийн код. Чиглэл: урьдчилсан мэдлэг → зорилтот concept |
| `relation` | `PRE`, `REL`, `CONTROL`, эсвэл холбоосгүй бол `—` / хоосон (мөр алгасагдана). `?` үлдээвэл импорт татгалзана |
| `confidence` | 1 / 2 / 3, эсвэл хоосон |
| `initial_weight` | 0–1, хоосон бол 1 |
| `note` | Үндэслэл (заавал биш) |

Асуулгын хуудасны 3-р хэсгийн (багшийн өөрийн) холбоосуудыг мөр болгон нэмнэ. 5-р хэсгийн хяналтын холбоосыг `CONTROL` гэж оруулна.

## Импортлох
```powershell
.venv\Scripts\python.exe manage.py import_graph <course> docs\graph_templates\concepts.csv docs\graph_templates\edges.csv --dry-run
.venv\Scripts\python.exe manage.py import_graph <course> docs\graph_templates\concepts.csv docs\graph_templates\edges.csv
```
Эхлээд `--dry-run`-аар шалгана. Тайланд H1, H2-ын нөхцөлүүд `OK` / `WARN` гэж гарна. Бодит оролдлого бүртгэгдэж эхэлсэн курсийн графыг команд өөрчлөхөөс татгалзана (урьдчилсан бүртгэл §2).
