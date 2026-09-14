"""Rapor karsiligi guncelleme-mimarisi testleri (offline, $0)."""
from nirvana import update_architecture as ua
from nirvana import data_sync as ds
from nirvana import knowledge_updater as ku
from nirvana import supply_guard as sg


def test_availability_formula():
    # A = MTBF / (MTBF + MTTR): MTTR duserse A yukselir
    assert ua.availability(720, 1.0) > ua.availability(720, 8.0) > 0.99


def test_strategy_table_complete():
    rows = ua.compare_strategies()
    names = {r["strategy"] for r in rows}
    assert names == {"blue_green", "canary", "rolling", "dark_launch"}


def test_select_low_budget_oracle_defaults_rolling():
    assert ua.select_strategy() == "rolling"
    assert ua.select_strategy(traffic="high") == "canary"
    assert ua.select_strategy(infra_budget="high") == "blue_green"


def test_canary_plan_gates():
    plan = ua.canary_plan(total=400)
    assert plan["waves"][-1]["pct"] == 100
    assert plan["waves"][-1]["count"] == 400
    assert "WAF_REJECT" in plan["waves"][0]["gate"]


def test_rolling_batches_keep_service_up():
    assert ua.rolling_batches(["a", "b", "c"], batch_size=1) == [["a"], ["b"], ["c"]]


def test_deploy_blocked_in_cooling():
    assert ua.deploy_allowed(in_cooling=True, deploys_last_24h=0)["allowed"] is False


def test_deploy_blocked_on_update_paralysis():
    r = ua.deploy_allowed(in_cooling=False, deploys_last_24h=9)
    assert r["allowed"] is False and r["reason"] == "update_paralysis_guard"


def test_cdc_append_only_dedup(isolated_state=None):
    evs = ds.cdc_events([{"domain": "a.com"}, {"domain": "a.com"}])
    assert evs[0]["event_id"] == evs[1]["event_id"]
    assert evs[0]["op"] == "upsert"


def test_saga_compensates_on_failure():
    calls: list[str] = []

    def ok():
        calls.append("s1")

    def fail():
        raise RuntimeError("boom")

    res = ds.saga_run([
        {"name": "s1", "run": ok, "compensate": lambda: calls.append("c1")},
        {"name": "s2", "run": fail, "compensate": lambda: calls.append("c2")},
    ])
    assert res["ok"] is False and res["failed_at"] == "s2"
    assert "c1" in calls


def test_rag_first_no_weight_change():
    out = ku.rag_answer("WooCommerce checkout sorunu")
    assert out["mode"] == "rag" and out["forget_risk"] == "none"


def test_supply_guard_sbom(tmp_path=None):
    info = sg.build_sbom()
    assert info["digest"] and info["modules"] > 0
    assert sg.verify_chain(expected_digest=info["digest"])["ok"] is True
    assert sg.verify_chain(expected_digest="0" * 64)["ok"] is False
