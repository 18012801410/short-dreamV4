"""全局配置：.env / 环境变量 → 类型化 Settings（TASK-002）。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.cn"

    runninghub_api_key: str = ""
    runninghub_base_url: str = "https://www.runninghub.cn"
    runninghub_workflow_video: str = "2098713358475288577"
    runninghub_workflow_image: str = "2098715929763995649"
    # 稳定版出图工作流的负向提示词节点号（如 7）；留空 = 极速版（无负向通道），不发送该字段
    image_negative_node: str = ""
    # 关键帧图生图工作流（Qwen-Image-Edit-2509 参考版，workerflow/QWEN图生图（Edit参考版单段））：
    # 配置后关键帧生成把在场资产卡作为参考图上传（锁脸），留空 = 关键帧走文生图工作流
    runninghub_workflow_image_edit: str = ""

    llm_api_key: str = ""
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_model: str = "glm-4.6"
    llm_max_tokens: int = 0  # 0=不传，由供应商用默认值；DeepSeek 建议 8192
    llm_timeout_sec: int = 120  # 结构化长输出（尤其推理模型）需放大
    h3_reference_video: bool = False  # 视频续接：referenceVideo 槽位语义待官方确认，默认关闭

    data_dir: Path = PROJECT_ROOT / "data"
    ffmpeg_path: str = "ffmpeg"
    poll_interval_sec: float = 2.0
    # 图片生成并发上限（image_gen + frame_gen 同时在跑的任务数，默认 2）：
    # RunningHub 账号并发队列满时会拒单（TASK_QUEUE_MAXED），把这里调到与
    # 账号额度一致可避免无谓的拒绝-退避循环。设置页可改（settings 表覆盖，
    # Worker 每轮领取时实时读取，改完即生效，无需重启）
    image_concurrency: int = 2

    @property
    def db_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    return settings
