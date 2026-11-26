from aidevteam.ui import UI


def test_stage_and_report(capsys):
    ui = UI()
    with ui.stage("SYNC", "проверка коммитов") as st:
        st.ok("новых коммитов нет")
        st.warn("что-то подозрительное")
    ui.skip("SPECIFY", "спека готова")
    ui.report("ЦИКЛ #1 ЗАВЕРШЁН", [("Сделано", "5 задач"), ("Дальше", "T-006")])
    out = capsys.readouterr().out
    assert "⏵ SYNC" in out and "✔ новых коммитов нет" in out
    assert "⚠" in out and "[skip]" in out
    assert "ЦИКЛ #1 ЗАВЕРШЁН" in out and "Сделано" in out
