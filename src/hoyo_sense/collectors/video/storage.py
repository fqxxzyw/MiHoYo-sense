"""不可覆盖的响应归档：字节原文与完整 JSON 分开，断点引用哈希。"""

import json
import os
from pathlib import Path
from uuid import uuid4

from ...storage import digest, read_json, check_digest


ID_KEYS = {"id", "aid", "bvid", "mid", "cid", "uid", "oid", "rpid", "rpid_str", "root", "parent", "sec_uid"}


def string_ids(value):
    """完整保留结构和所有记录，仅将 ID 字段改为字符串；字节原文另外保存。"""
    if isinstance(value, list):
        return [string_ids(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        is_id = key in ID_KEYS or key.endswith(("_id", "_id_str"))
        if is_id and item is not None and not isinstance(item, (dict, list)):
            if isinstance(item, bool) or not isinstance(item, (str, int)):
                raise ValueError("ID 字段不能为浮点数或布尔值。")
            result[key] = str(item)
        else:
            result[key] = string_ids(item)
    return result


def immutable_bytes(path, content):
    """先 fsync 临时文件，再硬链接发布；目标存在时失败，绝不覆盖旧响应。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid4().hex}.tmp")
    try:
        with temp.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def immutable_json(path, data):
    immutable_bytes(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def archive_response(root, stem, body, metadata):
    body_file = stem + (".html" if metadata.get("kind") == "detail" else ".body")
    record = {**metadata, "body_file": None, "sha256": None}
    if body is not None:
        immutable_bytes(root / body_file, body)
        record.update(body_file=body_file, sha256=digest(root / body_file))
    # sidecar 成功落盘后才能用于恢复；只有孤立 body 的情况下重新请求并另存。
    immutable_json(root / (stem + ".http.json"), record)
    return {"http_file": stem + ".http.json", **record}


def restore_response(root, record):
    path = root / record["http_file"]
    metadata = read_json(path)
    body_file = metadata["body_file"]
    if body_file is None:
        return None, metadata, record
    check_digest(root / body_file, metadata["sha256"])
    return (root / body_file).read_bytes(), metadata, record
