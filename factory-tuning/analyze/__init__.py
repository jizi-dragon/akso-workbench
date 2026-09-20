"""配置工厂调优的采集与分析（**不属于产品**，见 ../README.md）。

这个包只做分析：读 `../data/sessions/*/traffic.jsonl` → 出报告。
它可以**只读地** import 产品代码（端点表、蓝图模型），但产品代码**永远不许** import 它。
"""
