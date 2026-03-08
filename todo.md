## 代办事项
1. SOXL 聚焦网格与敏感度
    1. 运行并保存为 sensitivity_soxl_refined.json（网格：包含 RSI=40 以及 RSI=55–60，BB=1.00 与 BB=1.03–1.05）
2. 强化 GMMA 过滤
    1. 在operation_prediction_v2.py 中强制要求长周期 GMMA 发散向上才允许开仓
3. 分批建仓 与 移动止盈（研发）
    i. 先实现 2/3/4 批分批入场模拟，再添加 trailing-take-profit 原型
4. 后续（如通过上面筛选）
    i. 对通过的组合做蒙特卡洛稳健性测试并保存 montecarlo_soxl.json