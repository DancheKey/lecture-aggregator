# 全库重解析盘点 · 结论（2026-09-26）

## 分类计数

| 分类 | 页数 | 处置 |
|---|---|---|
| split_new | 26（判真约 20，垃圾拆分约 6） | A桶：判真页定向落库（逐页抽核）；垃圾拆分暴露守卫缺口，不落库、记观察 |
| split_count_change | 6 | 6页中4页 ⚠manual（5983/5949/7635/6973）——manual 基底锁定不受影响；3769 旧6→新7 更优可采纳 |
| split_reverted | 32 | 甄别后：海报/VLM 页8页 + 规则层伪回退（xz/207 实测全链路可拆）+ 真回退24页——**不要全量重爬**，真回退页维持存量 |
| parse_empty | 44 | 全量重爬会丢（回顾判定）；增量不受影响。保持增量模式即可 |
| unreachable | 1 | 记录在案 |
| field_diff | 1540 | speakerAffiliation 1289 处占大头（bio反推/LLM 值，重解析不填属预期）；lectureStart 113/lectureEnd 186 处含环节窗修正收益，抽查后可批量 |
| field_diff_llm | 548 | 预期内，不动 |
| unchanged | 1330 | — |

## A桶：split_new 判真页清单（定向落库候选，落库前逐页抽核）

- http://life.scnu.edu.cn/a/20260623/6777.html  1:早期发育进程中表观基因组重塑@2026-06-23 15:10:00; 2:早期发育进程中表观基因组重塑@2026-06-23 15:10:00
- http://lswh.scnu.edu.cn/a/20250328/54.html  1:中国社会科学文摘@2025-03-28 15:00:00; 2:历史学@2025-03-28 15:00:00
- http://ctld.scnu.edu.cn/a/20230923/4203.html  1:数字化转型赋能课程教学创新@2023-09-27 15:00:00; 2:基于知识图谱和AI构建智慧教学新生态@2023-09-27 16:10:00
- http://bd.scnu.edu.cn/a/20230618/56.html  1:GNSS应用与服务的性能指标当论付迎春@2023-06-19 15:05:00; 2:基于SuperMap GIS的智慧城市关@2023-06-19 15:25:00
- http://ctld.scnu.edu.cn/a/20220302/3973.html  1:精耕细作,润物无声——《政治经济学》课程@2022-03-10 14:45:00; 2:精耕细作,润物无声——《政治经济学》课程@2022-03-10 14:45:00
- http://psy.scnu.edu.cn/a/20201228/2064.html  1:大型人脑偏侧化研究@2020-12-28 15:00:00; 2:MEG deep source imag@2020-12-28 15:00:00
- http://psy.scnu.edu.cn/a/20181022/1595.html  1:Undoing the Unwanted@2018-10-25 09:30:00; 2:无意识的洞察:麻醉诱导的神经元网络活动的@2018-10-25 11:40:00
- http://em.scnu.edu.cn/a/20180615/6407.html  1:房价是如何影响流动人口定居意愿的 ?@2018-06-15 14:00:00; 2:城市与区域发展专题学术报告研讨会@2018-06-15 15:30:00
- http://em.scnu.edu.cn/a/20180106/6083.html  1:Do birthdates matter@2018-01-06 08:30:00; 2:数字化时代大学图书馆的发展方向@2018-01-06 09:00:00
- http://em.scnu.edu.cn/a/20180105/6075.html  1:Endogenous Horizonta@2018-01-05 14:00:00; 2:Comparison of energy@2018-01-05 15:30:00
- http://em.scnu.edu.cn/a/20170324/5493.html  1:领导者与风险偏好@2017-03-24 14:30:00; 2:To what extent does @2017-03-24 16:00:00
- http://em.scnu.edu.cn/a/20170107/5388.html  1:《分权制衡与司法独立:来自公安厅厅长兼任@2017-01-07 08:30:00; 2:《分权制衡与司法独立：来自公安@2017-01-07 08:30:00
- http://psy.scnu.edu.cn/a/20161212/1201.html  1:睡眠缺失的神经机制@2016-12-14 10:10:00; 2:Self – Other Differe@2016-12-14 10:35:00
- http://psy.scnu.edu.cn/a/20161212/1199.html  1:自闭症研究现状、进步与挑战@2016-12-13 14:30:00; 2:应用行为分析与自闭症谱系障碍@2016-12-13 14:30:00
- http://em.scnu.edu.cn/a/20161208/5295.html  1:Identity in Public G@2016-12-08 14:00:00; 2:E-Commerce Integrati@2016-12-08 15:30:00
- http://em.scnu.edu.cn/a/20151024/4031.html  1:非正规就业使大学毕业生陷入低收入陷阱了吗@2015-10-24 09:00:00; 2:共情陪伴与留守儿童精神健康研究@2015-10-24 10:00:00
- http://em.scnu.edu.cn/a/20150509/3993.html  1:城市农民工 " 地缘集聚 " 现象——基@2015-05-09 09:10:00; 2:我国城镇化要推进到什么程度:基于国际证据@2015-05-09 09:40:00
- http://psy.scnu.edu.cn/a/20150320/329.html  1:习得的刺激-反应联结调节认知控制的神经基@2015-03-24 16:00:00; 2:习得的刺激-反应联结调节认知控制的神经基@2015-03-24 16:00:00
- http://psy.scnu.edu.cn/a/20131129/221.html  1:统计与方法学的神话:青少年问题研究中的教@2013-12-01 08:30:00; 2:识别发展的个体差异:潜在类别 / 剖面分@2013-12-01 14:30:00
- http://psy.scnu.edu.cn/a/20130510/172.html  1:台湾临床心理学的过去、现在和未来@2013-05-13 15:00:00; 2:社会焦虑的心理病理机制与疗效探讨@2013-05-14 15:00:00

## D桶：垃圾拆分样本（守卫缺口观察，不落库）

- http://wxy.scnu.edu.cn/keyanxinxi/xueshuhuodong/2024/0913/3553.html  1:5—12@2024-09-28 10:55:00; 2:0—1 5@2024-09-28 15:10:00
- http://ggy.scnu.edu.cn/a/20201120/5326.html  1:播放学院宣传片@2020-11-21 08:45:00; 2:议题一:面向2035年的国家治理:机遇与@2020-11-21 09:00:00
- http://psy.scnu.edu.cn/a/20171113/1380.html  1::00-9:40成立仪式及揭牌@2017-11-16 09:40:00; 2:The Challenge of Gen@2017-11-16 15:50:00
- http://em.scnu.edu.cn/a/20171104/5916.html  1:主持人介绍活动安排及嘉宾@2017-11-04 09:00:00; 2:嘉宾交流互动@2017-11-04 09:00:00
- http://psy.scnu.edu.cn/a/20161114/1144.html  1:介绍参会嘉宾、 揭牌仪式、嘉宾致词@2016-11-18 03:00:00; 2:唐向东教授学术报告会@2016-11-18 03:20:00
- http://psy.scnu.edu.cn/a/20141124/311.html  1:不孕症与压力@2014-11-25 02:45:00; 2:失眠的神经行为病因模式@2014-11-25 04:20:00

## C桶：parse_empty 页清单（全量重爬会丢，维持增量）

- http://xz.scnu.edu.cn/a/20260312/318.html
- http://ctld.scnu.edu.cn/a/20251107/4476.html
- http://ibc.scnu.edu.cn/a/20250107/2779.html
- http://xz.scnu.edu.cn/a/20231205/116.html
- http://xz.scnu.edu.cn/a/20230505/94.html
- http://xz.scnu.edu.cn/a/20230329/87.html
- http://xz.scnu.edu.cn/a/20230328/86.html
- http://ctld.scnu.edu.cn/a/20221128/4106.html
- http://xz.scnu.edu.cn/a/20221210/78.html
- http://xz.scnu.edu.cn/a/20221117/70.html
- http://xz.scnu.edu.cn/a/20221027/66.html
- http://xz.scnu.edu.cn/a/20220924/60.html
- http://swc.scnu.edu.cn/collaborative/2021/1105/35.html
- http://gxb.scnu.edu.cn/a/20211008/22.html
- https://physics.scnu.edu.cn/a/20210603/11634.html
- http://ctld.scnu.edu.cn/a/20210517/1365.html
- http://ctld.scnu.edu.cn/a/20210422/1341.html
- http://ctld.scnu.edu.cn/a/20210311/1299.html
- http://ctld.scnu.edu.cn/a/20201218/1278.html
- http://ctld.scnu.edu.cn/a/20201127/1268.html
- http://ctld.scnu.edu.cn/a/20201113/1250.html
- http://ctld.scnu.edu.cn/a/20201016/1234.html
- http://ctld.scnu.edu.cn/a/20191220/1093.html
- http://geography.scnu.edu.cn/a/20191214/96.html
- http://geography.scnu.edu.cn/a/20191214/92.html
- http://ctld.scnu.edu.cn/a/20191125/1076.html
- http://ctld.scnu.edu.cn/a/20191022/1042.html
- http://ctld.scnu.edu.cn/a/20190920/1027.html
- http://geography.scnu.edu.cn/a/20190702/75.html
- http://ctld.scnu.edu.cn/a/20190619/998.html
- http://ctld.scnu.edu.cn/a/20190527/986.html
- http://ctld.scnu.edu.cn/a/20190428/968.html
- http://geography.scnu.edu.cn/a/20190329/61.html
- http://ctld.scnu.edu.cn/a/20190322/940.html
- http://ctld.scnu.edu.cn/a/20181126/892.html
- http://geography.scnu.edu.cn/a/20181119/55.html
- http://geography.scnu.edu.cn/a/20181111/54.html
- http://ctld.scnu.edu.cn/a/20180620/841.html
- http://ctld.scnu.edu.cn/a/20180525/704.html
- http://ctld.scnu.edu.cn/a/20180423/592.html
- https://lib.scnu.edu.cn/news/zuixingonggao/2018/0419/180.html
- http://psy.scnu.edu.cn/a/20170624/1322.html
- https://physics.scnu.edu.cn/a/20151117/797.html
- http://psy.scnu.edu.cn/a/20151204/903.html

## 真回退24页

详见 reparse-diff-20260926.json 的 split_reverted 条目。已实测：xz/207 全链路可拆（伪回退）；cs/4145、lswh/65 全链路也只 1 场（真回退，老拆分出自更早管线）——落库决策逐页人工判断。
