"""生成 config/users.yaml 用的口令哈希。

用法：
    python app/hash_password.py "新口令"

把输出的 pbkdf2_sha256$... 整串填到对应用户的 password_hash。
"""

from __future__ import annotations

import getpass
import hashlib
import secrets
import sys


def main() -> int:
    password = sys.argv[1] if len(sys.argv) > 1 else getpass.getpass("新口令: ")
    if not password:
        print("口令不能为空")
        return 1
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 260000
    )
    print(f"pbkdf2_sha256$260000${salt.hex()}${digest.hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
