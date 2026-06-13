from ai_coding_insights.redact import redact_secrets

def test_redacts_common_secrets():
    assert "sk-" not in redact_secrets("key sk-ABCDEFGHIJKLMNOuvwxyz1234")
    assert "ghp_" not in redact_secrets("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
    assert "REDACTED" in redact_secrets('password = "hunter2hunter2"')

def test_redacts_sk_proj_key():
    out = redact_secrets("key sk-proj-ABCDEFGHIJKLMNOP1234567890")
    assert "sk-proj-" not in out and "[REDACTED]" in out

def test_keeps_normal_text():
    assert redact_secrets("正常的中文说明，无密钥") == "正常的中文说明，无密钥"


def test_redacts_password_with_special_chars():
    # 旧值字符类 [A-Za-z0-9_\-]{8,} 遇首个特殊字符即截断，且 {8,} 要求连续 8 位——
    # 含 @!$#. 的真实口令会**整体不匹配 → 完全不脱敏**。这是隐私洞，必须挡住。
    for s in ('DB_PASSWORD=Str0ng!P@ss#2024',
              'password: "S3cr3t$value"',
              "token = 'abc.def.ghi.jklmnopqrs'"):
        assert "[REDACTED]" in redact_secrets(s), s
    assert "Str0ng" not in redact_secrets('DB_PASSWORD=Str0ng!P@ss#2024')


def test_redacts_more_secret_classes():
    for c in ("github_pat_" + "A" * 24, "gho_" + "B" * 30, "xoxb-" + "1" * 20,
              "AIza" + "C" * 35, "postgres://user:s3cr3tP@ss@prod-db:5432/app",
              "Authorization: Bearer " + "a.b" * 8):
        assert "[REDACTED]" in redact_secrets(c), c
    # 连接串里的口令不得残留
    assert "s3cr3tP" not in redact_secrets("postgres://user:s3cr3tP@ss@prod-db/app")


def test_redacts_pem_private_key_block():
    pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
           "MIIEowIBAAKCAQEArandomkeymaterialhere\n"
           "-----END RSA PRIVATE KEY-----")
    out = redact_secrets(pem)
    assert "[REDACTED]" in out and "keymaterial" not in out
