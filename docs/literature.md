# Уран зохиолын хайлт: H1 ба H2-ын онолын үндэслэл

*2026-10-09. Claude-ийн вэб хайлт.*

> ⚠️ **Баталгаажуулалтын түвшин.** Доорх бүтээлүүдийн **ном зүйн мэдээлэл болон хураангуйг** вэб хайлтаар шалгасан. **Бүрэн текстийг уншаагүй.** Иш татахаасаа өмнө эх бүтээлийг өөрөө уншиж, үр дүн болон ишлэлийг нь баталгаажуулна уу. Тодорхойгүй байгаа зүйлийг тэмдэглэсэн.

## 1. H2: шилжилт нийтлэг бүрэлдэхүүнээс хамаардаг (хамгийн шууд үндэслэл)

| Бүтээл | Гол санаа | H2-т хэрэглэх |
|---|---|---|
| **Singley, M. K., & Anderson, J. R. (1989).** *The Transfer of Cognitive Skill.* Harvard University Press. | Thorndike-ийн "ижил элемент" онолыг ACT*-ийн хүрээнд сэргээсэн. Нэг ур чадвар нөгөөд **нийтлэг production rule эсвэл declarative урьдчилсан мэдлэгийн хэмжээгээр** шилждэг. Гурван текст засварлагчтай туршилт энэ таамаглалыг дэмжсэн. | **H2 (+) чиглэлийн гол үндэслэл.** BFS нь Queue-гийн enqueue/dequeue үйлдлүүдийг, DFS нь Stack-ийнхийг шууд агуулдаг. Тиймээс бүтэц → алгоритм edge нь илүү олон нийтлэг элементтэй байна. **Шалгах зүйл:** "бүтэц → бүтэц" edge-ийн (Array → Linked list) нийтлэг элемент үнэхээр цөөн үү? Энэ нь хичээлийн агуулгаас хамаарна. |
| **Koedinger, K. R., Yudelson, M. V., & Pavlik, P. I. (2016).** Testing theories of transfer using error rate learning curves. *Topics in Cognitive Science, 8*(3), 589–609. | Шилжилтийн "faculty" (өргөн) ба "component" (нийтлэг бүрэлдэхүүнээр хязгаарлагдсан) онолыг 8 өгөгдлийн багц дээр загварчилж харьцуулсан. **Component загварууд илүү сайн таамагласан.** | Орчин үеийн, өгөгдөлд суурилсан нотолгоо. Шилжилт нь нийтлэг бүрэлдэхүүнээр дамждаг гэсэн H2-ын санааг дэмжинэ. Мөн таны аргачлалтай (сурах муруйгаас шилжилтийг тооцох) ойр. |
| **Koedinger, K. R., Corbett, A. T., & Perfetti, C. (2012).** The Knowledge-Learning-Instruction framework. *Cognitive Science.* | Knowledge component-ийг "гүйцэтгэлээс дүгнэгдэх танин мэдэхүйн нэгж" гэж тодорхойлсон. | Concept-ийг knowledge component гэж үзэх онолын суурь. **Шилжилтийн тухай хэсгийг шууд олж чадаагүй**, эх бүтээлээс шалгах. |
| **Barnett, S. M., & Ceci, S. J. (2002).** When and where do we apply what we learn? A taxonomy for far transfer. *Psychological Bulletin, 128*, 612–637. | Шилжилтийг агуулга ба контекстийн хэмжээсээр ангилсан. Ойрын болон алсын шилжилтийн ялгаа. | Таны edge-үүд ихэвчлэн **ойрын шилжилт** (нэг хичээл, нэг хэлбэр) гэдгийг тодорхойлоход хэрэглэнэ. ⚠️ Хэмжээсийн тоог хоёрдогч эх сурвалжууд зөрүүтэй өгсөн (жишээ нь 9), эх бүтээлээс шалгах. |

**H2 (−) чиглэлийн үндэслэл:** хайлтаар шууд нотолгоо олдсонгүй. Алгоритм нь бүтцээс гадна шинэ санаа (графаар аялах, зочилсон оройг тэмдэглэх) шаарддаг гэсэн аргумент нь Singley & Anderson-ий логикоор ч тайлбарлагдана: **нийтлэг бус элемент их бол шилжилт бага.** Хоёр чиглэлийг хооронд нь ялгах шийдвэрлэх асуулт бол "edge бүрийн нийтлэг ба шинэ элементийн харьцаа" юм.

## 2. H1: prerequisite бүтэц ба түүнийг өгөгдлөөс илрүүлэх

| Бүтээл | Гол санаа | H1-д хэрэглэх |
|---|---|---|
| **Scheines, R., Silver, E., & Goldin, I. (2014).** Discovering prerequisite relationships among knowledge components. *EDM 2014.* | Prerequisite холбоо өгөгдөлд **статистик ул мөр** үлдээдэг: холбоотой ур чадварууд хамааралтай болж, завсрын ур чадвар нь алсын холбоог "хаадаг". | Prerequisite edge нь RELATED-аас өөр, илүү хүчтэй статистик дохио өгнө гэсэн H1-ийн санааны аргачлалын үндэслэл. |
| **Käser, T., Klingler, S., Schwing, A. G., & Gross, M. (2014).** Beyond knowledge tracing: Modeling skill topologies with Bayesian networks. *ITS 2014*, LNCS 8474, 188–198. | Ур чадварын топологийг (prerequisite бүтэц) dynamic Bayesian network-д оруулахад 5 өгөгдлийн багцад BKT-ээс илүү таамагласан. | Prerequisite бүтцийг загварчлах нь таамаглалыг сайжруулдаг гэсэн нотолгоо. DKG-AP-ийн өмнөх бүтээл. |
| **Causal-Driven Skill Prerequisite Structure Discovery (AAAI 2024).** | Гүйцэтгэлийн өгөгдлөөс далд хувьсагчтай загвар болон нөхцөлт хараат бус байдлын тестээр prerequisite графыг илрүүлсэн. | Сүүлийн үеийн арга. Багшийн баталсан граф ба өгөгдлөөс илрүүлсэн графыг харьцуулах боломж. |

## 3. Загварын харьцуулалтын суурь (арга зүйн бүлэгт)

| Бүтээл | Хэрэглээ |
|---|---|
| **Corbett, A. T., & Anderson, J. R. (1994).** Knowledge tracing: Modeling the acquisition of procedural knowledge. *User Modeling and User-Adapted Interaction, 4*(4), 253–278. | BKT baseline. ⚠️ Он нь зарим эх сурвалжид 1995 гэж бий, ихэнх нь 1994. |
| **Pavlik, P. I., Cen, H., & Koedinger, K. R. (2009).** Performance Factors Analysis – A new alternative to knowledge tracing. *AIED 2009*, 531–538. | PFA baseline. Олон ур чадвартай бодлогыг дэмждэг. |
| **Pelánek, R. (2016).** Applications of the Elo rating system in adaptive educational systems. *Computers & Education.* DOI 10.1016/j.compedu.2016.03.017 | Онлайн чадварын гишүүн θ_u-ийн (Elo маягийн) үндэслэл. |
| **Nakagawa, H., Iwasawa, Y., & Matsuo, Y. (2019).** Graph-based knowledge tracing: Modeling student proficiency using graph neural network. *IEEE/WIC/ACM WI 2019*, 156–163. Сэтгүүлийн хувилбар: *Web Intelligence* 19 (2021), 87–102. | Граф дээр суурилсан knowledge tracing-ийн гол өмнөх бүтээл. DKG-AP-ийн шинэлэг байдлыг үүнтэй харьцуулна. |

## 4. Компьютерийн шинжлэх ухааны боловсрол: өгөгдлийн бүтцийн суралцахуй

| Бүтээл | Хэрэглээ |
|---|---|
| **Danielsiek, H., Paul, W., & Vahrenhold, J. (2012).** Detecting and understanding students' misconceptions related to algorithms and data structures. *SIGCSE 2012*, 21–26. | Хичээлийн контекст. **Хураангуйг олж чадаагүй.** |
| **Zingaro, D., Taylor, C., Porter, L., et al. (2018).** Identifying student difficulties with basic data structures. *ICER 2018.* | 249 оюутанд суурилсан, CS2-ын үндсэн өгөгдлийн бүтцийн хүндрэлүүд. |

⚠️ **Цоорхой:** "өгөгдлийн бүтэц → алгоритм" шилжилтийг **шууд хэмжсэн** судалгаа хайлтаар олдсонгүй. Энэ нь таны судалгааны **шинэлэг хувь нэмэр** болох боломжтой. Гэхдээ "олдсонгүй" гэдэг нь "байхгүй" гэсэн үг биш тул ACM Digital Library, ERIC зэрэг санд нэмэлт хайлт хийх нь зүйтэй.

## 5. Урьдчилсан бүртгэл

| Бүтээл | Хэрэглээ |
|---|---|
| **Gehlbach, H., & Robinson, C. D. (2018).** Mitigating illusory results through preregistration in education. *Journal of Research on Educational Effectiveness.* DOI 10.1080/19345747.2017.1387950 | Боловсролын судалгаанд урьдчилсан бүртгэл хийх үндэслэл ба практик заавар. ⚠️ Сэтгүүл болон оныг хайлтаар шууд баталгаажуулаагүй. |

## Эх сурвалжууд (хайлтаар шалгасан холбоосууд)
- Singley & Anderson (1989): https://Www.Gwern.net/doc/psychology/1989-singley-thetransferofcognitiveskill.pdf
- Koedinger, Yudelson & Pavlik (2016): https://digitalcommons.memphis.edu/facpubs/8666
- KLI framework (2012): https://kilthub.cmu.edu/articles/journal_contribution/The_knowledge-learning-instruction_framework_bridging_the_science-practice_chasm_to_enhance_robust_student_learning_/6470522
- Barnett & Ceci (2002), хоёрдогч эх сурвалж: https://poorvucenter.yale.edu/print/4471
- Scheines, Silver & Goldin (2014): https://www.stat.cmu.edu/~brian/nynke/726-2021/project%20HCI%20Prereqs%20(Elaine,%20Smeet%20&%20Zhou)/articles/ScheinesSilverGoldin-EDM2014.pdf
- Käser et al. (2014): https://www.cs.toronto.edu/~aschwing/KaeserEtAl_ITS2014.pdf ; https://infoscience.epfl.ch/record/278641?ln=en
- CSPS (AAAI 2024): https://ojs.aaai.org/index.php/AAAI/article/view/30046
- Corbett & Anderson: https://link.springer.com/doi/10.1007/BF01099821
- Pavlik, Cen & Koedinger (2009): https://www.stat.cmu.edu/~brian/nynke/726-2021/project%20HCI%20Prereqs%20(Elaine,%20Smeet%20&%20Zhou)/articles/Pavlik,%20Cen%20Kodeinger%2009.pdf
- Pelánek (2016): https://www.fi.muni.cz/~xpelanek/publications/CAE-elo.pdf
- Nakagawa et al. (2019/2021): https://journals.sagepub.com/doi/pdf/10.3233/WEB-210458
- Danielsiek et al. (2012): https://cris-portal.uni-muenster.de/portal/en/publication/21949584
- Zingaro et al. (2018): https://www.cs.swarthmore.edu/~kwebb/papers/DataStructuresDifficulties.pdf
- Gehlbach & Robinson (2018): https://forrt.org/curated_resources/mitigating-illusory-results-through-prer/

## 6. 1-р бүлэгт нэмсэн эх сурвалжууд (2026-10-09, вэб хайлтаар ном зүйн мэдээллийг шалгасан, бүрэн текстийг уншаагүй)

| Бүтээл | Гол санаа | Тэмдэглэл |
|---|---|---|
| **Piech, C., Bassen, J., Huang, J., Ganguli, S., Sahami, M., Guibas, L. J., & Sohl-Dickstein, J. (2015).** Deep knowledge tracing. *NeurIPS 28*, 505–513. | RNN-д суурилсан DKT, BKT-ээс илүү таамагласан | arXiv хувилбарт 2-р зохиогч "Spencer" гэж бичигдсэн |
| **Khajah, M., Lindsey, R. V., & Mozer, M. C. (2016).** How deep is knowledge tracing? *EDM 2016*, 94–101. | Өргөтгөсөн BKT нь DKT-тэй ялгагдахгүй үр дүн өгсөн (recency, ур чадварын төстэй байдал, чадварын ялгаа) | EDM 2016-ийн шилдэг өгүүлэл |
| **Pelánek, R. (2017).** Bayesian knowledge tracing, logistic models, and beyond. *UMUAI 27*(3–5), 313–350. DOI 10.1007/s11257-017-9193-2 | Суралцагчийн загваруудын тойм | |
| **Cen, H., Koedinger, K. R., & Junker, B. (2006).** Learning factors analysis. *ITS 2006*, LNCS 4053, 164–175. | AFM-ийн эх сурвалж | |
| **Pavlik, P. I., & Anderson, J. R. (2005).** Practice and forgetting effects on vocabulary memory. *Cognitive Science 29*(4), 559–586. | Мартах ба spacing-ийн activation загвар | 1-р бүлэгт хараахан ашиглаагүй. Мартах загварын (3.1) үндэслэлд нэмж болно |
| **Chen, P., Lu, Y., Zheng, V. W., & Pian, Y. (2018).** Prerequisite-driven deep knowledge tracing. *ICDM 2018*, 39–48. | Prerequisite хосыг гүн KT-д хязгаарлалт болгосон | |
| **Tong, S., Liu, Q., Huang, W., Huang, Z., Chen, E., Liu, C., Ma, H., & Wang, S. (2020).** Structure-based knowledge tracing: An influence propagation view. *ICDM 2020*, 541–550. | Шилжилтийн санаагаар concept хооронд нөлөө тархаадаг (SKT) | ⚠️ **DKG-AP-ийн Wᵀ-тэй хамгийн ойр.** Мэдээллийг хоёрдогч эх сурвалжаас авсан. Эх бүтээлийг заавал уншина |
| **Ghosh, A., Heffernan, N., & Lan, A. S. (2020).** Context-aware attentive knowledge tracing. *KDD 2020*. DOI 10.1145/3394486.3403282 | Attention ба тайлбартай бүрэлдэхүүн (AKT) | |
| **Jain, S., & Wallace, B. C. (2019).** Attention is not explanation. *NAACL 2019*, 3543–3556. | Attention жинг тайлбар гэж үзэх нь буруу байж болно | |
| **Doshi-Velez, F., & Kim, B. (2017).** Towards a rigorous science of interpretable machine learning. arXiv:1702.08608 | Тайлбарлах чадварыг үнэлэх ангилал | |

Эх сурвалжууд: https://papers.neurips.cc/paper/2015/hash/bac9162b47c56fc8a4d2a519803d51b3-Abstract.html ; https://arxiv.org/pdf/1604.02416 ; https://is.muni.cz/publication/1404956 ; https://dblp1.uni-trier.de/rec/conf/its/CenKJ06.html ; https://digitalcommons.memphis.edu/facpubs/8387 ; https://aic-fe.bnu.edu.cn/docs/20190108101850881476.pdf ; https://arxiv.org/pdf/2105.15106 ; https://arxiv.org/abs/2007.12324v1 ; https://aclanthology.org/N19-1357/ ; https://arxiv.org/abs/1702.08608v2
