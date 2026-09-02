#!/usr/bin/env python3
"""敏感字段加密存储（AES-256-GCM + PBKDF2 密钥派生）。

用途：history/ 下的文件（notified.json、valid_keys.json）需要 commit 回仓库，
但其中含仓库名/文件路径（泄露点定位信息）。用对称加密后只存密文，
密钥存 GitHub Actions Secrets，公开仓库里无人能解密。

算法：
- 密钥派生：PBKDF2-HMAC-SHA256（用户密码 + 随机 salt，210000 轮迭代）
- 加密：AES-256-GCM（认证加密，同时保证机密性与完整性）

环境变量：
- KEYSTORE_PASSPHRASE：加密口令（存 GitHub Secrets）
  未设置时脚本以"明文模式"运行（本地可用，CI 中必须设置）
"""
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
except ImportError:
    AESGCM = None

ITERATIONS = 210_000
SALT_LEN = 16
NONCE_LEN = 12

PASSPHRASE = os.environ.get("KEYSTORE_PASSPHRASE", "").strip()
# 未配置口令时降级为明文（保证本地脚本不报错，CI 中应始终配置）
ENABLED = bool(PASSPHRASE) and AESGCM is not None


def _derive(salt):
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )
    return kdf.derive(PASSPHRASE.encode("utf-8"))


def encrypt(plaintext):
    """加密字符串 → base64 字符串（salt+nonce+ciphertext）。未启用时原样返回。"""
    if not ENABLED or plaintext is None:
        return plaintext
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = _derive(salt)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(salt + nonce + ct).decode("ascii")


def decrypt(token):
    """解密 base64 字符串 → 明文。未启用或格式不符时原样返回。"""
    if not ENABLED or token is None:
        return token
    try:
        raw = base64.b64decode(token)
        if len(raw) < SALT_LEN + NONCE_LEN + 16:
            return token  # 可能是历史明文数据
        salt, nonce, ct = raw[:SALT_LEN], raw[SALT_LEN:SALT_LEN + NONCE_LEN], raw[SALT_LEN + NONCE_LEN:]
        key = _derive(salt)
        return AESGCM(key).decrypt(nonce,ct, None).decode("utf-8")
    except Exception:
        return token  # 解密失败：视为历史明文，避免程序崩溃


def encrypt_fields(record, fields):
    """对 dict 中指定字段加密（原地修改并返回）。"""
    for f in fields:
        if f in record:
            record[f] = encrypt(record[f])
    return record


def decrypt_fields(record, fields):
    """对 dict 中指定字段解密（原地修改并返回）。"""
    for f in fields:
        if f in record:
            record[f] = decrypt(record[f])
    return record
