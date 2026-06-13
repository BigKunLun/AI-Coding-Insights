import re

# 密钥脱敏：batch JSON 是 LLM 层（用户本机 cc，会经 Anthropic API）的直接输入，
# 凭证类 token 必须在出规则层前就地抹掉。这是该路径上唯一一道秘钥网。
# 取向：宁可过度脱敏（连标签一起抹），不可漏放——漏一个 live credential 即违反隐私铁律。
# 顺序：具体形态（前缀锚定/JWT/连接串/PEM）在前各自精确匹配，最后再跑宽口径的
# 「标签=值」兜底，避免兜底先吃掉标签把后面的真 token 留在外面。
_PATTERNS = [
    # PEM 私钥块（含 RSA/EC/OPENSSH 等各类 BEGIN ... PRIVATE KEY）
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"),
    # JWT（三段 base64url，header 恒以 eyJ 起）
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}"),
    # 连接串内嵌口令：scheme://user:pass@host → 整段抹（host 亦可能含业务名）
    re.compile(r"(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?|https?|ftp)"
               r"://[^\s:/@]+:[^\s@]+@[^\s]+"),
    re.compile(r"sk-[A-Za-z0-9-]{16,}"),                # OpenAI / Anthropic 风格 key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),          # GitHub PAT/OAuth/server/refresh/user token
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),        # GitHub fine-grained PAT
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),        # Slack token
    re.compile(r"AKIA[0-9A-Z]{16}"),                    # AWS access key id
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),              # Google API key
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=\-]{10,}"),  # Authorization: Bearer <token>
    # 兜底：带标签的密钥 api_key/token/password/secret/auth(orization)/... = <value>。
    # 值放宽到「非空白非引号的连续串(≥6)」，覆盖含 @!$.#/: 等特殊字符的真实口令——
    # 旧版 [A-Za-z0-9_\-]{8,} 遇首个特殊字符即截断，且因 {8,} 而对短前缀整体不匹配，
    # 导致 `password=Str0ng!P@ss` 这类**完全不脱敏**（隐私洞）。
    re.compile(r"(?i)(?:api[\s_-]?key|access[\s_-]?key|secret[\s_-]?key|client[\s_-]?secret"
               r"|token|password|passwd|pwd|secret|authorization|auth)"
               r"\s*[=:]\s*['\"]?[^\s'\"]{6,}"),
]


def redact_secrets(text: str) -> str:
    for p in _PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text
