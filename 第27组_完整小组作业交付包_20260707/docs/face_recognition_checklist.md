# 人脸识别 5 真 5 假验收记录

## 数据来源

- 数据集：LFW / Labeled Faces in the Wild, lfw-funneled
- 用途：课堂原型演示的人脸录入与判别测试
- 判定目标：录入 5 人，不录入 5 人；录入人员 allow，未录入人员 deny

## 已录入人员

- `P01`: Ron Dittemore (`Ron_Dittemore`)
- `P02`: Kim Jong-Il (`Kim_Jong-Il`)
- `P03`: Intisar Ajouri (`Intisar_Ajouri`)
- `P04`: Petria Thomas (`Petria_Thomas`)
- `P05`: Michael Winterbottom (`Michael_Winterbottom`)

## 未录入测试人员

- `U01_test`: James Brosnahan (`James_Brosnahan`)
- `U02_test`: Enrica Fico (`Enrica_Fico`)
- `U03_test`: Edith Masai (`Edith_Masai`)
- `U04_test`: Reyyan Uzuner (`Reyyan_Uzuner`)
- `U05_test`: Carin Koch (`Carin_Koch`)

## 10 张测试图判别结果

| 样本 | 类型 | 人名 | 期望 | 实际 | 匹配人员 | 相似度 | 结果 |
| --- | --- | --- | --- | --- | --- | ---: | --- |
| `P01_test` | authorized | Ron Dittemore | allow | allow | Ron Dittemore | 0.9210 | 通过 |
| `P02_test` | authorized | Kim Jong-Il | allow | allow | Kim Jong-Il | 0.8868 | 通过 |
| `P03_test` | authorized | Intisar Ajouri | allow | allow | Intisar Ajouri | 0.8498 | 通过 |
| `P04_test` | authorized | Petria Thomas | allow | allow | Petria Thomas | 0.8462 | 通过 |
| `P05_test` | authorized | Michael Winterbottom | allow | allow | Michael Winterbottom | 0.8439 | 通过 |
| `U01_test` | unknown | James Brosnahan | deny | deny | - | 0.2019 | 通过 |
| `U02_test` | unknown | Enrica Fico | deny | deny | - | 0.2246 | 通过 |
| `U03_test` | unknown | Edith Masai | deny | deny | - | 0.2434 | 通过 |
| `U04_test` | unknown | Reyyan Uzuner | deny | deny | - | 0.2451 | 通过 |
| `U05_test` | unknown | Carin Koch | deny | deny | - | 0.2522 | 通过 |

## 结论

- 总样本数：10
- 通过：10
- 失败：0
- 5 真 5 假：通过
- 老师最低 2 真 1 假：使用 `P01_test`、`P02_test`、`U01_test` 演示。
