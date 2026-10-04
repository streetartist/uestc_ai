"""Declarative submission requirements shared by all challenge types."""

from copy import deepcopy
import re
from urllib.parse import urlsplit
from pathlib import PurePosixPath
import zipfile


ATTACHMENT_EXTENSIONS = {"md", "pdf", "csv", "json", "zip", "tar", "gz", "png", "jpg", "jpeg", "webp", "mp4"}


def validate_submission_schema(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"fields", "field_definitions", "readme_required", "readme_template", "attachments"}:
        raise ValueError("invalid submission schema")
    fields = value.get("fields", [])
    if not isinstance(fields, list) or len(fields) > 40 or any(not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) for key in fields) or len(fields) != len(set(fields)):
        raise ValueError("invalid submission field keys")
    definitions = value.get("field_definitions", {})
    if not isinstance(definitions, dict) or set(definitions) - set(fields):
        raise ValueError("submission field definitions must match fields")
    for spec in definitions.values():
        if not isinstance(spec, dict) or set(spec) - {"label", "type", "required", "help", "placeholder"}:
            raise ValueError("invalid submission field definition")
        if spec.get("type", "text") not in {"text", "url", "textarea"} or type(spec.get("required", False)) is not bool:
            raise ValueError("invalid submission field type or requirement")
        for key in ("label", "help", "placeholder"):
            if key in spec and (not isinstance(spec[key], str) or len(spec[key]) > 1000):
                raise ValueError("invalid submission field description")
    if type(value.get("readme_required", False)) is not bool:
        raise ValueError("readme_required must be a boolean")
    if not isinstance(value.get("readme_template", ""), str) or len(value.get("readme_template", "")) > 100000:
        raise ValueError("invalid README template")
    requirements = value.get("attachments", [])
    if not isinstance(requirements, list) or len(requirements) > 10:
        raise ValueError("invalid attachment requirements")
    seen = set()
    for spec in requirements:
        if not isinstance(spec, dict) or set(spec) - {"key", "label", "extensions", "min_count", "max_count", "required_files"} or not {"key", "label", "extensions", "min_count", "max_count"} <= set(spec):
            raise ValueError("invalid attachment requirement")
        key = spec["key"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) or key in seen:
            raise ValueError("invalid attachment requirement key")
        seen.add(key)
        if not isinstance(spec["label"], str) or not 1 <= len(spec["label"]) <= 120:
            raise ValueError("invalid attachment requirement label")
        extensions = spec["extensions"]
        if not isinstance(extensions, list) or not extensions or any(not isinstance(ext, str) or ext not in ATTACHMENT_EXTENSIONS for ext in extensions) or len(extensions) != len(set(extensions)):
            raise ValueError("invalid attachment extensions")
        low, high = spec["min_count"], spec["max_count"]
        if type(low) is not int or type(high) is not int or not 0 <= low <= high <= 20 or high == 0:
            raise ValueError("invalid attachment counts")
        required_files = spec.get("required_files", [])
        if not isinstance(required_files, list) or len(required_files) > 20 or any(not isinstance(name, str) or not name or len(name) > 200 or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name for name in required_files):
            raise ValueError("invalid required package files")
        if required_files and extensions != ["zip"]:
            raise ValueError("required package files need ZIP attachments")
    return deepcopy(value)


def validate_submission_materials(schema, data, assets, formal, upload_folder=None):
    """Drafts may be incomplete; formal submissions must satisfy every requirement."""
    fields = data.get("fields", {})
    if not isinstance(fields, dict) or any(not isinstance(key, str) or not isinstance(value, str) or len(value) > 20000 for key, value in fields.items()):
        raise ValueError("submission fields must contain text values")
    if not isinstance(data.get("readme_md"), str):
        raise ValueError("submission README must be text")
    if not formal:
        return
    if schema.get("readme_required") and not data["readme_md"].strip():
        raise ValueError("请填写作品介绍或研究报告。")
    for key, spec in schema.get("field_definitions", {}).items():
        value = fields.get(key, "").strip()
        label = spec.get("label") or key
        if spec.get("required") and not value:
            raise ValueError(f"请填写：{label}。")
        if value and spec.get("type") == "url":
            try:
                url = urlsplit(value)
                valid = url.scheme in {"https", "http"} and bool(url.hostname)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(f"{label}需要有效的 HTTP/HTTPS 地址。")
    for spec in schema.get("attachments", []):
        matching = [asset for asset in assets if asset.original_name.lower().rsplit(".", 1)[-1] in spec["extensions"]]
        count = len(matching)
        if not spec["min_count"] <= count <= spec["max_count"]:
            formats = "/".join("." + extension for extension in spec["extensions"])
            raise ValueError(f"{spec['label']}需要 {spec['min_count']}—{spec['max_count']} 个 {formats} 附件。")
        if spec.get("required_files"):
            for asset in matching:
                try:
                    with zipfile.ZipFile(upload_folder / asset.storage_name) as archive:
                        entries = archive.infolist()
                        if len(entries) > 256 or sum(item.file_size for item in entries) > 64 * 1024 * 1024:
                            raise ValueError("程序包最多 256 项，解压总大小不能超过 64 MB。")
                        names = {item.filename for item in entries if not item.is_dir()}
                        missing = set(spec["required_files"]) - names
                        if missing:
                            raise ValueError("程序包缺少：" + ", ".join(sorted(missing)))
                except (OSError, zipfile.BadZipFile) as error:
                    raise ValueError("程序包不是可读取的 ZIP 文件，请重新上传。") from error
