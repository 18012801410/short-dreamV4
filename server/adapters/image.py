"""ImageProvider 适配器（TASK-006）：RunningHub QWEN 出图工作流实现。

文生图工作流（TASK-006/006 实测）：节点 6=提示词、节点 8=宽高、节点 5=seed，
输出节点 18（SaveImage）。契约见 AI_SPEC 附录 B。

图生图 Edit 工作流（TASK-031 关键帧一致性，Qwen-Image-Edit-2509 参考版，
workerflow/QWEN图生图（Edit参考版单段））：配置 settings.runninghub_workflow_image_edit
且调用方传入 reference_files 时，走该工作流——参考图（在场资产卡）经上传接口换
fileName 填入节点 10/11/12，正向/负向写节点 6/7 的 prompt 字段，其余节点编号与
文生图版一致。参考图不足 3 张时用第一张复用填充（避免 LoadImage 空输入报错）。
"""

from __future__ import annotations

import random
from pathlib import Path

from server.adapters.runninghub import RunningHubClient, RunningHubError
from server.domain.providers import GeneratedImage, MediaUsage
from server.infra.config import Settings

IMAGE_WORKFLOW_NODE_PROMPT = "6"  # CLIPTextEncode.text / TextEncodeQwenImageEditPlus.prompt
IMAGE_WORKFLOW_NODE_SIZE = "8"  # EmptyLatentImage / EmptySD3LatentImage.width/height
IMAGE_WORKFLOW_NODE_SEED = "5"  # KSampler.seed
IMAGE_EDIT_NODE_NEGATIVE = "7"  # TextEncodeQwenImageEditPlus.prompt（负向）
IMAGE_EDIT_REF_NODES = ("10", "11", "12")  # LoadImage.image（参考图 1-3）
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


class RunningHubImage:
    """同步实现，与 Worker 同步 handler 协议一致。

    negative_node 非空时（稳定版工作流的负向 CLIPTextEncode 节点号），
    negative 文本会作为该节点的 text 字段一起提交；空则不发送（极速版无负向通道）。
    """

    def __init__(
        self,
        *,
        client: RunningHubClient,
        workflow_id: str,
        seed_range: tuple[int, int] = (1, 2**31),
        negative_node: str = "",
        edit_workflow_id: str = "",
    ) -> None:
        self._client = client
        self._workflow_id = workflow_id
        self._seed_range = seed_range
        self._negative_node = negative_node
        self._edit_workflow_id = edit_workflow_id

    def generate(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        dest: Path,
        seed: int | None = None,
        negative: str | None = None,
        reference_files: list[Path] | None = None,
    ) -> GeneratedImage:
        # seed 缺省随机（每次出图有变化）；传入固定 seed + 同提示词 = 可复现
        seed = seed if seed is not None else random.randint(*self._seed_range)
        workflow_id = self._workflow_id
        node_info_list = [
            {
                "nodeId": IMAGE_WORKFLOW_NODE_SIZE,
                "fieldName": "width",
                "fieldValue": width,
            },
            {
                "nodeId": IMAGE_WORKFLOW_NODE_SIZE,
                "fieldName": "height",
                "fieldValue": height,
            },
            {
                "nodeId": IMAGE_WORKFLOW_NODE_SEED,
                "fieldName": "seed",
                "fieldValue": seed,
            },
        ]
        if reference_files and self._edit_workflow_id:
            # Edit 参考版：参考图上传换 fileName 填节点 10/11/12（不足复用**最后一张**），
            # 正向/负向写节点 6/7 的 prompt 字段。
            # 复制最后一张而不是第一张：参考顺序是 [角色卡, …, 场景卡]，缺槽时复制的
            # 应当是场景——同一张人物卡占据两个槽位，会把这个人"复印"进画面
            # （实测 S02G02：乙的卡被复制后，画里出现两个一模一样的乙）。
            workflow_id = self._edit_workflow_id
            file_names = [self._client.upload(Path(p)) for p in reference_files[:3]]
            while len(file_names) < len(IMAGE_EDIT_REF_NODES):
                file_names.append(file_names[-1])
            node_info_list.append(
                {
                    "nodeId": IMAGE_WORKFLOW_NODE_PROMPT,
                    "fieldName": "prompt",
                    "fieldValue": prompt,
                }
            )
            if negative:
                node_info_list.append(
                    {
                        "nodeId": IMAGE_EDIT_NODE_NEGATIVE,
                        "fieldName": "prompt",
                        "fieldValue": negative,
                    }
                )
            for node_id, file_name in zip(IMAGE_EDIT_REF_NODES, file_names, strict=True):
                node_info_list.append(
                    {
                        "nodeId": node_id,
                        "fieldName": "image",
                        "fieldValue": file_name,
                    }
                )
        else:
            node_info_list.append(
                {
                    "nodeId": IMAGE_WORKFLOW_NODE_PROMPT,
                    "fieldName": "text",
                    "fieldValue": prompt,
                }
            )
            if negative and self._negative_node:
                node_info_list.append(
                    {
                        "nodeId": self._negative_node,
                        "fieldName": "text",
                        "fieldValue": negative,
                    }
                )
        task_id = self._client.create_task(workflow_id, node_info_list)
        outputs = self._client.wait_for_success(task_id, label="image")
        file = _pick_image(outputs)
        if file is None:
            raise RunningHubError(
                "BAD_RESPONSE",
                "outputs 中没有图片产物",
                details={"outputs": str(outputs)[:500]},
            )
        content = self._client.download(file["fileUrl"])
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return GeneratedImage(
            provider_task_id=task_id,
            seed=seed,
            file_url=file["fileUrl"],
            dest=dest,
            size_bytes=len(content),
            usage=MediaUsage(
                coins=str(file.get("consumeCoins") or "0"),
                task_seconds=str(file.get("taskCostTime") or "0"),
                raw=file,
            ),
        )


def _pick_image(outputs: list[dict]) -> dict | None:
    for item in outputs or []:
        url = str(item.get("fileUrl", "")).lower()
        if url.endswith(IMAGE_SUFFIXES):
            return item
    return None


def build_image_provider_from_settings(settings: Settings) -> RunningHubImage:
    if not settings.runninghub_api_key:
        raise RunningHubError("AUTH", "RUNNINGHUB_API_KEY 未配置（.env 或环境变量）")
    client = RunningHubClient(
        base_url=settings.runninghub_base_url,
        api_key=settings.runninghub_api_key,
        poll_interval=settings.poll_interval_sec * 2 or 5.0,
    )
    return RunningHubImage(
        client=client,
        workflow_id=settings.runninghub_workflow_image,
        negative_node=settings.image_negative_node,
        edit_workflow_id=settings.runninghub_workflow_image_edit,
    )


__all__ = ["RunningHubImage", "build_image_provider_from_settings"]
