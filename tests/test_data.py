from survey_coder.data import load_data


def test_load_data_preserves_leading_zeroes_in_configured_id(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text("ResponseID,Comment\n001,comment\n", encoding="utf-8")

    frame = load_data(str(path), "ResponseID")

    assert frame.loc[0, "ResponseID"] == "001"
