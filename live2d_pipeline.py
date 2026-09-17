#!/usr/bin/env python3
"""AI-assisted image-to-Live2D workspace.

The script intentionally leaves final mesh/physics authoring to Cubism Editor.
"""

import argparse
import base64
import json
import hashlib
import time
import uuid
import zipfile
import os
import re
import shlex
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import httpx
from openai import APIConnectionError, InternalServerError, OpenAI
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
# The SDK only retries before the response starts; a drop mid-body (peer closed the
# chunked stream) or a gateway 5xx surfaces here and is worth one fresh request.
STREAM_RETRY_ERRORS = (httpx.TransportError, APIConnectionError, InternalServerError)


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def client() -> OpenAI:
    load_env()
    key = os.getenv("APEXIN_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        raise SystemExit("缺少 APEXIN_API_KEY，请在 .env 中设置。")
    base_url = os.getenv("APEXIN_BASE_URL", "https://api.apexin.ai/v1").rstrip("/")
    timeout = float(os.getenv("APEXIN_TIMEOUT_SECONDS", "600"))
    retries = int(os.getenv("APEXIN_MAX_RETRIES", "1"))
    if timeout <= 0 or retries < 0:
        raise SystemExit("APEXIN_TIMEOUT_SECONDS 必须为正数，APEXIN_MAX_RETRIES 不能为负数。")
    return OpenAI(api_key=key, base_url=base_url, timeout=timeout, max_retries=retries)


def slug(text: str) -> str:
    value = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", text, flags=re.UNICODE).strip("-")
    return value[:48] or "character"


def new_workspace(name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUTPUTS / f"{slug(name)}-{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_image_response(data, path: Path) -> None:
    item = data.data[0]
    b64 = getattr(item, "b64_json", None)
    url = getattr(item, "url", None)
    if b64:
        path.write_bytes(base64.b64decode(b64))
        return
    if url:
        urllib.request.urlretrieve(url, path)
        return
    raise RuntimeError("图片接口返回中没有 b64_json 或 url。")


def prepare_input(image: Path, output: Path) -> None:
    """Composite transparency on white for diffusion; retain the original separately."""
    with Image.open(image) as source:
        rgba = ImageOps.exif_transpose(source).convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, "white")
        canvas.alpha_composite(rgba)
        canvas.convert("RGB").save(output, format="PNG")


def generate(args) -> None:
    api = client()
    workspace = new_workspace(args.name or args.prompt[:24])
    model = os.getenv("IMAGE_MODEL", "gpt-image-2.5-sunburst")
    prompt = (
        args.prompt
        + "\n要求：正面或接近正面、角色完整、背景干净、四肢不要被裁切，"
        "便于后续拆分脸、眼睛、嘴巴、前发、后发、衣服和饰品。不要文字、水印和复杂背景。"
    )
    print(f"调用 {model} 生成角色图…")
    result = api.images.generate(model=model, prompt=prompt, size=args.size, n=1)
    image_path = workspace / "01_generated.png"
    save_image_response(result, image_path)
    prepare_input(image_path, workspace / "01_input_white.png")
    (workspace / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    with Image.open(image_path) as actual:
        metadata = dict(model=model, requested_size=args.size, actual_size=actual.size,
                        image_mode=actual.mode, image_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
                        input_file="01_input_white.png", prompt_file="prompt.txt")
    (workspace / "generation.json").write_text(json.dumps(metadata, indent=2))
    print(f"已保存：{image_path}")


def image_data_uri(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix, "image/png")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def extract_json(text: str):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def plan(args) -> None:
    api = client()
    image = Path(args.image).expanduser().resolve()
    if not image.exists():
        raise SystemExit(f"找不到图片：{image}")
    workspace = Path(args.output).expanduser().resolve() if args.output else image.parent
    workspace.mkdir(parents=True, exist_ok=True)
    model = os.getenv("ASTRA_MODEL", "gpt-6-astra")
    schema = {
        "character_summary": "string",
        "regions": {
            "face": ["x", "y", "width", "height"],
            "left_eye": ["x", "y", "width", "height"],
            "right_eye": ["x", "y", "width", "height"],
            "mouth": ["x", "y", "width", "height"]
        },
        "layers": [{"name": "string", "order": 0, "purpose": "string", "needs_inpaint": True, "cubism_parameter": "string"}],
        "repair_tasks": ["string"],
        "parameters": [{"name": "string", "range": "string", "affected_layers": ["string"]}],
        "physics": [{"name": "string", "input": "string", "output": "string", "layers": ["string"]}],
        "acceptance_poses": ["string"],
        "risks": ["string"],
    }
    instruction = (
        "你是 Live2D 制作总监。分析这张角色图，输出严格 JSON，不要 Markdown。"
        "目标是交给 See-through 拆层和 Live2D Cubism Editor 5.3 使用。"
        "按变形行为而不是只按语义拆分；指出被遮挡区域需要补画的位置。"
        "regions 中的坐标必须是输入图像像素坐标，原点左上，字段是 x,y,width,height；"
        "无法可靠定位时填 null，不要猜超出图像范围的坐标。"
        f"JSON 结构如下：{json.dumps(schema, ensure_ascii=False)}"
    )
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": [
            {"type": "text", "text": "请分析此角色图。"},
            {"type": "image_url", "image_url": {"url": image_data_uri(image)}},
        ]},
    ]
    retries = int(os.getenv("ASTRA_STREAM_RETRIES", "1"))
    if retries < 0:
        raise SystemExit("ASTRA_STREAM_RETRIES 不能为负数。")
    print(f"调用 {model} 分析拆层和绑定计划…")
    for attempt in range(retries + 1):
        try:
            text, finish_reason = stream_plan(api, model, messages)
            break
        except STREAM_RETRY_ERRORS as error:
            if attempt == retries:
                raise
            delay = 5 * (attempt + 1)
            print(f"Astra 流式响应中断（{type(error).__name__}），{delay} 秒后重新请求…", flush=True)
            time.sleep(delay)
    (workspace / "layer_plan_response.txt").write_text(text, encoding="utf-8")
    if finish_reason != "stop":
        raise RuntimeError("Astra stream ended without a complete plan")
    data = extract_json(text)
    (workspace / "layer_plan.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_plan_markdown(data, workspace / "layer_plan.md")
    print(f"已保存：{workspace / 'layer_plan.json'}")


def stream_plan(api, model, messages):
    """Stream one planning response and return its text with the finish reason."""
    result = api.chat.completions.create(
        model=model,
        reasoning_effort=os.getenv('ASTRA_REASONING_EFFORT','low'),
        max_completion_tokens=8000,
        temperature=0.2,
        stream=True,
        messages=messages,
    )
    chunks=[]
    finish_reason=None
    with result:
        for chunk in result:
            if chunk.choices:
                choice=chunk.choices[0]
                if choice.delta.content:
                    chunks.append(choice.delta.content)
                if choice.finish_reason:
                    finish_reason=choice.finish_reason
    return ''.join(chunks), finish_reason


def write_plan_markdown(data, path: Path) -> None:
    out = ["# Live2D 拆层与绑定计划", "", data.get("character_summary", ""), "", "## 图层"]
    for layer in data.get("layers", []):
        out.append(f"- `{layer.get('name')}`：{layer.get('purpose', '')}；顺序 {layer.get('order')}；参数 `{layer.get('cubism_parameter', '')}`；补画：{layer.get('needs_inpaint')}")
    out += ["", "## 补画任务"]
    out += [f"- {item}" for item in data.get("repair_tasks", [])]
    out += ["", "## 参数"]
    for item in data.get("parameters", []):
        out.append(f"- `{item.get('name')}` {item.get('range')}: {', '.join(item.get('affected_layers', []))}")
    out += ["", "## 物理"]
    for item in data.get("physics", []):
        out.append(f"- `{item.get('name')}`：{item.get('input')} → {item.get('output')}；图层：{', '.join(item.get('layers', []))}")
    out += ["", "## 验收姿态"]
    out += [f"- {item}" for item in data.get("acceptance_poses", [])]
    out += ["", "## 风险"]
    out += [f"- {item}" for item in data.get("risks", [])]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def decompose(args) -> None:
    image = Path(args.image).expanduser().resolve()
    root = Path(args.see_through).expanduser().resolve()
    if not image.exists():
        raise SystemExit(f"找不到图片：{image}")
    script = root / "inference/scripts/inference_psd.py"
    if not script.exists():
        raise SystemExit(f"找不到 See-through 推理脚本：{script}")
    workspace = Path(args.output).expanduser().resolve() if args.output else image.parent / "see_through"
    workspace.mkdir(parents=True, exist_ok=True)
    cmd = [args.python or sys.executable, str(script), "--srcp", str(image), "--save_to_psd",
           "--save_dir", str(workspace), "--tblr_split", "--resolution", str(args.resolution)]
    if args.group_offload:
        cmd.append("--group_offload")
    print("运行 See-through：", " ".join(cmd))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "common") + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run(cmd, cwd=root, env=env, check=True)
    (workspace / "README_NEXT.md").write_text(
        "See-through 已完成推理。下一步：\n\n"
        "1. 检查输出 PSD 的图层和隐藏补画；\n"
        "2. 运行 seethrough-live2d-pipeline 的 reproject/refine_edges；\n"
        "3. 在 Cubism Editor 5.3 中建立网格、参数和物理；\n"
        "4. 按 layer_plan.md 的验收姿态逐项检查。\n",
        encoding="utf-8",
    )
    print(f"See-through 输出目录：{workspace}")


def ssh(host, command, capture=False):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host,
                           shlex.join(command)], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None)


def unpack_result(archive, workspace, expected):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Downloaded ZIP checksum mismatch")
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            dest = (workspace / info.filename).resolve()
            if workspace != dest and workspace not in dest.parents:
                raise RuntimeError("Unsafe archive path")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("Archive symlinks are not supported")
        z.extractall(workspace)
    hashes = json.loads((workspace / "artifacts.json").read_text())
    for name, expected_hash in hashes.items():
        path = (workspace / name).resolve()
        if workspace not in path.parents:
            raise RuntimeError("Unsafe manifest path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError("Artifact checksum mismatch: " + name)
    psds = list(workspace.rglob("*.psd"))
    if not psds:
        raise RuntimeError("No PSD in returned artifacts")
    return psds


def remote_decompose(args) -> None:
    image = Path(args.image).expanduser().resolve()
    if not image.is_file():
        raise SystemExit(f"找不到图片：{image}")
    load_env()
    host = os.getenv("REMOTE_SSH_HOST", "seetacloud")
    remote_root = os.getenv("REMOTE_ROOT", "/root/live2d-ai").rstrip("/")
    remote_python = os.getenv("REMOTE_PYTHON", "/root/miniconda3/bin/python")
    repo = os.getenv("REMOTE_SEE_THROUGH", remote_root + "/third_party/see-through").rstrip("/")
    model_root = os.getenv("REMOTE_MODEL_ROOT", "/root/autodl-tmp/live2d-ai/models").rstrip("/")
    workspace = Path(args.output).expanduser().resolve() if args.output else image.parent / "see_through_remote"
    workspace.mkdir(parents=True, exist_ok=True)
    state_file = workspace / "remote_job.json"
    fingerprint = dict(image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                       resolution=args.resolution, group_offload=args.group_offload,
                       host=host, repo=repo, python=remote_python, model_root=model_root)
    if state_file.exists() and not args.new_run:
        state = json.loads(state_file.read_text())
        if state["fingerprint"] != fingerprint:
            raise SystemExit("输入或设置变化，请指定新输出目录或 --new-run。")
        job = state["job"]
        print("恢复远程任务：" + job, flush=True)
    else:
        job = remote_root + "/jobs/" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        remote_image = job + "/input" + image.suffix.lower()
        command = [remote_python, "-u", job + "/infer_staged.py",
                   "--image", remote_image, "--save-dir", job + "/output",
                   "--resolution", str(args.resolution), "--models", model_root,
                   "--manifest", job + "/models-manifest.json"]
        if args.group_offload:
            command.append("--group_offload")
        spec = dict(cwd=repo, command=command, env={"PYTHONPATH": repo + "/common",
                    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                    "PYTHONUNBUFFERED": "1"})
        state = dict(job=job, fingerprint=fingerprint)
        (workspace / "spec.json").write_text(json.dumps(spec, indent=2))
        ssh(host, ["mkdir", "-p", job + "/output"])
        subprocess.run(["scp", "-q", str(image), f"{host}:{remote_image}"], check=True)
        subprocess.run(["scp", "-q", str(workspace / "spec.json"),
                        str(ROOT / "scripts/remote_worker.py"), str(ROOT / "scripts/infer_staged.py"),
                        str(ROOT / "scripts/models-manifest.json"), f"{host}:{job}/"], check=True)
        # Save task identity before launch so interruptions cannot create a duplicate silently.
        (workspace / "status.json").write_text(json.dumps({"state": "queued"}))
        subprocess.run(["scp", "-q", str(workspace / "status.json"), f"{host}:{job}/"], check=True)
        state_file.write_text(json.dumps(state, indent=2))
        launch = shlex.join(["nohup", remote_python, "-u", job + "/remote_worker.py", job])
        subprocess.run(["ssh", host, launch + " > " + shlex.quote(job + "/worker.out") +
                        " 2>&1 < /dev/null &"], check=True)
        print("已启动远程任务：" + job, flush=True)
    previous = None
    while True:
        try:
            raw = ssh(host, ["cat", job + "/status.json"], capture=True).stdout
            status = json.loads(raw)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            print("暂时无法读取任务状态；服务器任务会继续，可重新运行本命令恢复。", flush=True)
            raise
        if status["state"] != previous:
            print("远程状态：" + status["state"], flush=True)
            previous = status["state"]
        if status["state"] == "failed":
            subprocess.run(["scp", "-q", f"{host}:{job}/inference.out", str(workspace)], check=True)
            (workspace / "status.json").write_text(json.dumps(status, indent=2))
            raise SystemExit(status["error"] + "；详情见 " + str(workspace / "inference.out"))
        if status["state"] == "complete":
            break
        time.sleep(10)
    archive = workspace / "result.zip"
    subprocess.run(["scp", "-q", f"{host}:{job}/result.zip", str(archive)], check=True)
    subprocess.run(["scp", "-q", f"{host}:{job}/inference.out", str(workspace)], check=True)
    psds = unpack_result(archive, workspace, status["archive_sha256"])
    (workspace / "status.json").write_text(json.dumps(status, indent=2))
    print("已下载并校验 PSD：" + ", ".join(str(p) for p in psds), flush=True)


def remote_status(args) -> None:
    load_env()
    host = os.getenv("REMOTE_SSH_HOST", "seetacloud")
    subprocess.run(["ssh", host, "nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader"], check=True)


def checklist(args) -> None:
    api = client()
    image = Path(args.image).expanduser().resolve()
    output = Path(args.output).expanduser().resolve() if args.output else image.parent / "qa_report.md"
    model = os.getenv("ASTRA_MODEL", "gpt-6-astra")
    prompt = (
        "你是 Live2D QA。检查这张角色图是否适合图生 Live2D，输出简洁 Markdown。"
        "必须包括：可拆层部件、需要补画区域、闭眼/张嘴/转头风险、建议 Cubism 参数、人工验收清单。"
    )
    result = api.chat.completions.create(
        model=model,
        temperature=0.1,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_data_uri(image)}}]},
        ],
    )
    output.write_text(result.choices[0].message.content or "", encoding="utf-8")
    print(f"已保存 QA 报告：{output}")


def open_cubism(args) -> None:
    target = str(Path(args.path).expanduser().resolve())
    app = os.getenv("CUBISM_EDITOR", "/Applications/Live2D Cubism 5.3/Live2D Cubism Editor 5.3.app")
    subprocess.run(["open", "-a", app, target], check=True)
    print(f"已打开 Cubism：{target}")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 图生 Live2D 工作台")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("generate", help="调用 Image-2.5 生成角色图")
    p.add_argument("--prompt", required=True)
    p.add_argument("--name")
    p.add_argument("--size", default="1024x1024")
    p.set_defaults(func=generate)
    p = sub.add_parser("plan", help="调用 Astra 生成拆层与绑定计划")
    p.add_argument("--image", required=True)
    p.add_argument("--output")
    p.set_defaults(func=plan)
    p = sub.add_parser("decompose", help="调用本地 See-through 拆层并输出 PSD")
    p.add_argument("--image", required=True)
    p.add_argument("--see-through", required=True)
    p.add_argument("--output")
    p.add_argument("--group-offload", action="store_true")
    p.add_argument("--resolution", type=int, default=1280)
    p.add_argument("--python", help="See-through 环境的 Python")
    p.set_defaults(func=decompose)
    p = sub.add_parser("remote-decompose", help="通过 SSH 在远程 GPU 上调用 See-through")
    p.add_argument("--image", required=True)
    p.add_argument("--output")
    p.add_argument("--group-offload", action="store_true")
    p.add_argument("--resolution", type=int, default=1280)
    p.add_argument("--new-run", action="store_true")
    p.set_defaults(func=remote_decompose)
    p = sub.add_parser("remote-status", help="查看远程 GPU 状态")
    p.set_defaults(func=remote_status)
    p = sub.add_parser("checklist", help="调用 Astra 生成视觉 QA 报告")
    p.add_argument("--image", required=True)
    p.add_argument("--output")
    p.set_defaults(func=checklist)
    p = sub.add_parser("open-cubism", help="打开 Cubism Editor 5.3")
    p.add_argument("--path", required=True)
    p.set_defaults(func=open_cubism)
    from scripts.body_motion import add_arguments, execute
    p = sub.add_parser('body-motion', help='生成呼吸、倾斜、手臂和裙摆的可编辑绑定与动作')
    add_arguments(p)
    p.set_defaults(func=execute)
    p = sub.add_parser('build', help='输入提示词或图片，运行完整图生 Live2D 流程')
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--prompt')
    source.add_argument('--image')
    p.add_argument('--name')
    p.add_argument('--output', type=Path, help='指定新的空输出目录')
    p.add_argument('--size', default='1024x1536')
    p.add_argument('--resolution', type=int, default=1280)
    p.add_argument('--group-offload', action='store_true')
    p.add_argument('--reuse-decomposition')
    p.add_argument('--reuse-plan', help='复用同一输入图的 layer_plan.json，跳过 Astra 请求')
    p.add_argument('--reuse-expressions', help='复用同一输入图目录中的 expression_eyes.png 和 expression_mouth.png')
    p.set_defaults(func=lambda args: __import__('scripts.auto_build', fromlist=['run']).run(args))
    args = parser.parse_args()
    load_env()
    args.func(args)


if __name__ == "__main__":
    main()
