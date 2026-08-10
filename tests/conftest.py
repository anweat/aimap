"""测试全局配置:使用独立临时数据库,避免污染 data/aimap.db 与测试间干扰。

必须在任何 app 模块导入前设置环境变量。
"""
import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="aimap_test_"), "test.db")
os.environ["AIMAP_DB_PATH"] = _TMP_DB
os.environ["AIMAP_LLM_PROVIDER"] = "mock"
os.environ["AIMAP_ARXIV_DELAY"] = "0"
