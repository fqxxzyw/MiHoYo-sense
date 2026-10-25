# 公共预测后端

`server.py` 同时提供本地 HTTP API 和 `app/` 网页，`prediction_service.py` 负责模型发现、缓存和算法分发。
使用 Python 标准库启动，无需安装新的 Web 框架。

已实现：

- 接收界面传来的算法选择和评论文本。
- 根据算法调用 `src/hoyo_sense/modeling/<algorithm>/predict.py` 中的预测函数。
- 从 `outputs/models/<algorithm>/<run>/` 加载对应模型。
- 统一返回评论、情感标签和情感名称。

API 负责连接界面与算法，模型定义、训练和评估继续留在各算法目录。
在项目根目录运行：

```powershell
& "D:\Anaconda3\envs\hoyosense\python.exe" -u scripts/09_serve_app.py
```

打开 `http://127.0.0.1:8765`，按 Ctrl+C 停止。可用 `--port 8766` 更改端口。

- `GET /api/health`：服务状态。
- `GET /api/models`：算法状态、可选模型版本与 dev 指标。
- `POST /api/predict`：`{"model_id":"tfidf_logistic/<运行目录>","texts":["评论一","评论二"]}`。

预测结果包含逐条标签、情感名称、类别计数和服务处理耗时。每次最多 100 条，单条最多 5000 字。
只允许选择模型列表中已发现、已注册的模型，不接受任意文件路径。
模型按文件时间和大小校验缓存，最多保留三个版本；接口不训练模型，也不改写数据或模型。

目前已注册逻辑回归、随机森林、fastText 和 BERT，界面可切换有权重的算法及其模型版本。
LR/RF 沿用 `pipeline.joblib`；fastText/BERT 根据 `artifact.json` 找到原生模型及原始/量化/蒸馏类型。
缓存同时检查目录内 tokenizer、配置与权重的变化。可选算法延迟导入依赖，不影响只安装 sklearn 的用法。
新算法完成后，在 `prediction_service.ADAPTERS` 和 `LOADERS` 注册预测函数及加载函数；
模型扫描与加载格式也需与该算法的实际保存格式适配，不能仅靠预留目录就视为可用。
