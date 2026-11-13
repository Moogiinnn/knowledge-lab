# Курс оруулах жишээ

Энэ хавтас нь нэг жижиг курсийг системд оруулах бүрэн жишээ. Ойлголтууд нь ICPC багцын (40 ойлголт) 8-аас авсан хэсэг тул ICPC-ийн бодит файлууд яг ийм бүтэцтэй байна.

```
import_example/
├── concepts.csv          ойлголтууд: code, name, kind, description
├── edges.csv             холбоосууд: source, target, relation, confidence, initial_weight, note
├── students.csv          оюутнууд: username (оюутны код; нэр бичихгүй)
└── problems/
    ├── problems.csv      бодлогууд: code, title, kind, difficulty, concepts, language, accepted, hints, note
    ├── bfs-shortest/
    │   ├── statement.md  оюутанд харагдах өгүүлбэр
    │   └── tests/        sample-1.in/.out, sample-2.in/.out (оюутанд харагдана), 03, 04 (нууц)
    ├── prefix-sum-queries/
    └── stack-order/      SHORT бодлого: зөвхөн statement.md
```

## problems.csv-ийн баганууд

| Багана | Утга | Жишээ |
|---|---|---|
| code | Латин үсэг, тоо, `-`; 40 тэмдэгт хүртэл; хавтасны нэртэй ижил | `bfs-shortest` |
| title | Оюутанд харагдах нэр | `Хамгийн богино зам (BFS)` |
| kind | `CODE` (код илгээнэ) эсвэл `SHORT` (богино хариулт) | `CODE` |
| difficulty | Багшийн үнэлсэн хүндрэл 1–5 (ICPC_hundrel_jin_unelgee.xlsx-ийн «Эцсийн хүндрэл») | `3` |
| concepts | `код:жин;код:жин`, нийлбэр 1. Жингүй бичвэл (`stack;queue`) тэнцүү жин | `queue:0.4;bfs:0.4;graph:0.2` |
| language | `CPP`, `C`, `PYTHON` (CODE-д; хоосон бол CPP) | `CPP` |
| accepted | SHORT бодлогын зөв хариултууд, `\|`-ээр тусгаарлана | `2` |
| hints | Зөвлөмжүүд дарааллаар, `\|`-ээр тусгаарлана | `Дараалал ашигла.\|dist-ийг -1-ээр эхлүүл.` |
| note | Өөрийн тэмдэглэл (системд орохгүй) | |

Тест файлууд: `NAME.in` ба `NAME.out` хос, нэрийн дарааллаар ажиллана. Нэр нь `sample`-ээр эхэлбэл оюутанд харагдана, бусад нь нууц.

## Сервер дээр оруулах дараалал

Файлуудыг серверт хуулсны дараа (Lightsail SSH → ⚙ → Upload file, эсвэл `git pull`):

```bash
cd /srv/knowledge-lab
K="sudo -u klab .venv/bin/python manage.py"

# 1. Курс: /admin/ → Хичээлүүд → Нэмэх (код icpc-2027, is synthetic-ийг ЧЕКЛЭХГҮЙ, зөвшөөрлийн текст)
# 2. Граф (эхлээд шалгах, дараа нь оруулах)
$K import_graph icpc-2027 docs/import_example/concepts.csv docs/import_example/edges.csv --dry-run
$K import_graph icpc-2027 docs/import_example/concepts.csv docs/import_example/edges.csv
# 3. Бодлогууд (эхлээд шалгах; --publish-гүй бол DRAFT болж орно)
$K import_problems icpc-2027 docs/import_example/problems --dry-run
$K import_problems icpc-2027 docs/import_example/problems --publish
# 4. Оюутнууд (нууц үгтэй CSV .local/ хавтсанд гарна)
$K import_students icpc-2027 docs/import_example/students.csv
```

Алдаатай мөр байвал команд **юу ч бичихгүй**, бүх алдааг нэг дор жагсаана. Засаад дахин ажиллуулна.

## Анхаарах

- Граф бодит оролдлого бүртгэгдэж эхэлмэгц царцана (урьдчилсан бүртгэл §2). Графыг өгөгдөл цуглуулахаас өмнө эцэслэнэ.
- Нийтэлсэн бодлогыг дахин импортлоход алгасна. Засвар оруулах бол `--new-version` (шинэ хувилбар үүснэ; өмнөх оролдлогууд хуучин хувилбартаа үлдэнэ).
- Жишээ курсийн граф жижиг (8 ойлголт) тул `import_graph` «concepts 8 (need 20–40)» гэж анхааруулна. ICPC-ийн 40 ойлголттой графад энэ анхааруулга гарахгүй.
- Жишээ бодлогын тест кейсийг жишиг C++ шийдлээр шалгасан (7/7).
