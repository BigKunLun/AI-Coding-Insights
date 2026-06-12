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
