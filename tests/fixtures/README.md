# 合成夹具

`generate_fixtures.py` 生成 SPEC 13.2 / 13.3 所需的九家合成公司，写入 `universe/universe.json`。

这些数字全部是为了验证规则分类而构造的，不是真实上市公司数据。不要把其中的价格或估值当作市场事实。

重新生成：

```bash
python tests/fixtures/generate_fixtures.py
```
