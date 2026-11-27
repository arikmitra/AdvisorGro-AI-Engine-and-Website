import time

import pytest

from advisorgro.core.config import Settings
from advisorgro.core.errors import Unauthorized, ValidationFailed
from advisorgro.core.hashing import normalise_html
from advisorgro.core.ssrf import assert_public_url, normalise_domain
from advisorgro.db.session import session_scope
from advisorgro.security import links, webhooks
from advisorgro.security.activity import log_activity, verify_chain
from advisorgro.security.crypto import Crypto, LocalKeyProvider, mask_email
from advisorgro.security.policy import authorize
from advisorgro.security.principal import Principal
from advisorgro.security.tokens import TokenVerifier, mint_dev_token


def P(*roles, tenant="t1", staff=False, **kw):
    return Principal(user_id="u1", tenant_id=None if staff else tenant, roles=frozenset(roles), is_staff=staff, **kw)


@pytest.mark.parametrize("role,action,resource,expected", [
    ("cco", "approve", "campaign_approval", True), ("firm_admin", "approve", "campaign_approval", False), ("marketing", "approve", "campaign_approval", False),
    ("marketing", "edit", "campaign_drafts", True), ("cco", "edit", "campaign_drafts", False), ("read_only", "edit", "campaign_drafts", False),
    ("marketing", "pause", "campaign_pause", False), ("marketing", "request", "campaign_pause", True), ("firm_admin", "pause", "campaign_pause", True),
    ("firm_admin", "edit", "billing", True), ("cco", "view", "billing", False), ("firm_admin", "export", "leads", True), ("cco", "export", "leads", False),
    ("read_only", "view", "activity_log", True), ("marketing", "view", "activity_log", False), ("advisor", "view", "nonexistent", False)])
def test_permission_matrix(role, action, resource, expected):
    assert bool(authorize(P(role), action, resource, resource_tenant="t1")) is expected


def test_cross_tenant_denied_for_everyone_including_cco():
    assert not authorize(P("cco"), "view", "reports", resource_tenant="t2")


def test_lead_masking_and_ownership():
    assert authorize(P("marketing"), "view", "leads", resource_tenant="t1").masked
    assert not authorize(P("cco"), "view", "leads", resource_tenant="t1").masked
    assert authorize(P("advisor"), "view", "leads", resource_tenant="t1").own_only


def test_staff_rules():
    assert not authorize(P("platform_admin", "cco", staff=True), "approve", "campaign_approval", resource_tenant="t1")
    assert not authorize(P("support", staff=True), "view", "leads", resource_tenant="t1")  # needs ticket + consent
    d = authorize(P("support", staff=True, ticket="T-1", consent=True), "view", "leads", resource_tenant="t1")
    assert d and d.masked
    assert not authorize(P("engineer", staff=True), "view", "leads", resource_tenant="t1")
    assert authorize(P("engineer", staff=True), "view", "leads", resource_tenant="t1", break_glass_tenants=frozenset({"t1"}))  # break-glass only


def test_crypto_round_trip_tenant_binding_and_keyed_hash():
    k = LocalKeyProvider(Settings().kek_b64, Settings().hmac_key_b64)
    cr = Crypto(k)
    blob = cr.encrypt("t1", "pat@example.com")
    assert "pat" not in blob and cr.decrypt("t1", blob) == "pat@example.com"
    from cryptography.exceptions import InvalidTag
    with pytest.raises(InvalidTag):  # a blob copied to another tenant will not decrypt
        cr.decrypt("t2", blob)
    assert cr.keyed_hash("A@x.com ") == cr.keyed_hash("a@x.com") and mask_email("pat@x.com") == "p***@x.com"


def test_prod_refuses_dev_secrets():
    with pytest.raises(ValueError, match="unsafe settings"):
        Settings(env="prod")


def test_tokens_signature_expiry_audience():
    s = Settings()
    v = TokenVerifier(s)
    assert v.verify(mint_dev_token(s, sub="u", tenant_id="t", roles=["cco"])).tenant_id == "t"
    with pytest.raises(Unauthorized):
        v.verify(mint_dev_token(s, sub="u", exp=int(time.time()) - 10))
    with pytest.raises(Unauthorized):
        v.verify(mint_dev_token(s, sub="u", aud="someone-else"))
    with pytest.raises(Unauthorized):
        v.verify(mint_dev_token(Settings(jwt_secret="y" * 40), sub="u"))


def test_webhook_signature_and_signed_links():
    body = b'{"a":1}'
    ts, sig = webhooks.sign("s", body)
    webhooks.verify("s", body, ts, sig)
    with pytest.raises(Unauthorized):
        webhooks.verify("s", body + b" ", ts, sig)
    link = links.sign_link("sec", "t1/abc/report.html", 60)
    exp, sg = int(link.split("exp=")[1].split("&")[0]), link.split("sig=")[1]
    links.verify_link("sec", "t1/abc/report.html", exp, sg)
    with pytest.raises(Unauthorized):
        links.verify_link("sec", "t1/other.html", exp, sg)
    with pytest.raises(Unauthorized):
        links.verify_link("sec", "t1/abc/report.html", exp, sg, now=exp + 1)


def test_ssrf_guard():
    for bad in ("http://127.0.0.1/", "http://169.254.169.254/latest", "http://localhost/", "http://10.0.0.5/", "file:///etc/passwd", "http://metadata.google.internal/"):
        with pytest.raises(ValidationFailed):
            assert_public_url(bad, resolve=False)
    assert normalise_domain("https://www.Firm.com/x?y=1") == "firm.com"
    with pytest.raises(ValidationFailed):
        normalise_domain("192.168.1.1")


def test_activity_log_is_hash_chained_and_tamper_evident(c):
    with session_scope(c.sm, system=True) as s:
        for i in range(3):
            log_activity(s, actor="u", action=f"a{i}", tenant_id="t1")
    with session_scope(c.sm, system=True) as s:
        assert verify_chain(s)
    from sqlalchemy import text
    with c.db_engine.begin() as conn:  # a direct DB edit (bypassing the ORM guard) is detected
        conn.execute(text("UPDATE activity_log SET action='forged' WHERE seq=2"))
    with session_scope(c.sm, system=True) as s:
        assert not verify_chain(s)


def test_html_normalisation_ignores_markup_and_scripts_but_not_text_changes():
    assert normalise_html("<p>Guaranteed</p>") != normalise_html("<p>Not guaranteed</p>")
    a = normalise_html("<html><head><script>x=1</script></head><body><p>Hello   <b>world</b></p><a href='/a'>A</a><a href='/b'>B</a></body></html>")
    b = normalise_html("<body>\n<p>Hello world</p><a href='/a'>A</a><a href='/b'>B</a><script>y=2</script></body>")
    assert a == b


def test_pii_never_reaches_logs_or_redaction_holds():
    from advisorgro.core.redaction import redact, redact_obj
    assert "pat@example.com" not in redact("contact pat@example.com or 512-555-0100") and "[phone]" in redact("512-555-0100")
    assert redact_obj({"api_key": "sk-123", "note": "ssn 123-45-6789"}) == {"api_key": "[secret]", "note": "ssn [ssn]"}


def test_prod_refuses_every_dev_secret():
    from advisorgro.core.config import DEV_SECRETS
    ok = dict(env="prod", kek_b64="a2V5LWEta2V5LWEta2V5LWEta2V5LWEta2V5LWE=", hmac_key_b64="a2V5LWIta2V5LWIta2V5LWIta2V5LWIta2V5LWI=", jwt_mode="jwks", use_fakes=False, inline_worker=False,
              webhook_secret_calendar="c1" * 16, webhook_secret_sms="s1" * 16, webhook_secret_ads="a1" * 16, link_secret="l1" * 16)
    Settings(**ok)  # a fully configured prod is accepted
    for field in ("webhook_secret_sms", "link_secret"):
        with pytest.raises(ValueError, match="unsafe settings"):
            Settings(**{**ok, field: sorted(DEV_SECRETS)[0]})
