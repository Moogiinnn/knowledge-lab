# Data dictionary: research event export

`manage.py export_events <course>` хэрэглэнэ. Нэг мөр = нэг submit. Мөрүүд `timestamp`, дараа нь `event_id`-аар эрэмбэлэгдсэн.

| Багана | Төрөл | Утга |
|---|---|---|
| event_id | UUID | Давхардалгүй үйл явдлын ID |
| student_id | text | `p_` + HMAC-SHA256 pseudonym (24 hex) |
| course_id | text | Курсын код |
| session_id | UUID | Нэвтрэлт бүрийн санамсаргүй ID (Django session key биш) |
| timestamp | ISO-8601 UTC | Сервер хүлээн авсан мөч, миллисекундээр, `Z` |
| question_id | text | Бодлогын код |
| question_version | int | Хариулсан хувилбарын дугаар |
| difficulty | 1–5 | Тухайн хувилбарын хүндрэл (d_q-ийн анхны утга) |
| attempt_index | int ≥ 1 | Тухайн оюутны тухайн бодлогын оролдлогын дугаар, цоорхойгүй |
| correct | 0/1/хоосон | Эцсийн дүн (сүүлийн GradeRevision, байхгүй бол автомат дүн). Хоосон = үнэлгээ хүлээгдэж байна |
| score | [0,1]/хоосон | Эцсийн хэвийнжүүлсэн оноо |
| tests_passed, tests_total | int/хоосон | CODE бодлогын тестийн үр дүн. SHORT бодлогод хоосон |
| auto_correct, auto_score | | Submit үеийн анхны автомат дүн |
| grading_status | AUTO / PENDING / REVISED:<source> | `REVISED:code_runner` нь Docker sandbox-ийн үнэлгээ, `REVISED:teacher` нь багшийн үнэлгээ. CODE бодлогын `auto_*` нь хоосон |
| grader_version | text | Үнэлгээний дүрмийн хувилбар (`short-1` гэх мэт) |
| grade_revisions | int | Засварын тоо |
| response_time_sec | int/хоосон | Client хэмжсэн идэвхтэй хугацаа (таб харагдаж байсан үе) |
| timing_source | text | `client_active_visible` / `client_missing` / `client_invalid` |
| server_elapsed_sec | int/хоосон | Сервер хэмжсэн хугацаа: бодлогын хуудсыг (өмнөх submit-ээс хойш) анх нээснээс submit хүртэл. Идэвхгүй хугацааг багтаана. `response_time_sec`-ийг шалгахад ашиглана |
| hint_count | int ≥ 0 | Энэ submit хүртэл тухайн бодлогод үзсэн hint-ийн тоо (серверт тоологдсон) |
| assistance_flag | 0/1/хоосон | Өөрөө мэдүүлсэн тусламж. Хоосон = мэдүүлээгүй |
| assistance_types | `;`-ээр тусгаарласан | AI, peer, teacher, material, other |
| concepts | JSON | `[{"concept":code,"weight":a_qi}]`, жингийн нийлбэр = 1 |
| is_synthetic | 0/1 | Үндсэн export-д үргэлж 0 |

**Prediction-д ашиглах анхааруулга:** `response_time_sec`, `hint_count`, `attempt_index`, `assistance_*` нь тухайн оролдлогын evidence юм. Тэдгээрийг зөвхөн update алхамд ашиглана. Тухайн оролдлогын prediction-ий оролтод ашиглахгүй.
