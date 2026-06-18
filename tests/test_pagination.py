from app.main import PAGE_SIZE, pagination


def test_pagination_limits_rows_and_preserves_filters():
    pager = pagination(2, 75, "/accounts", q="prod api", status="healthy")

    assert PAGE_SIZE == 30
    assert pager["page"] == 2
    assert pager["offset"] == 30
    assert pager["total_pages"] == 3
    assert "q=prod+api" in pager["next_url"]
    assert "status=healthy" in pager["next_url"]
    assert "page=3" in pager["next_url"]


def test_pagination_clamps_out_of_range_page():
    assert pagination(-5, 10, "/")["page"] == 1
    assert pagination(99, 31, "/")["page"] == 2


def test_model_pagination_uses_independent_query_parameter():
    pager = pagination(
        2,
        61,
        "/accounts/1",
        page_param="model_page",
        page=3,
        model_q="gpt vision",
    )
    assert "page=3" in pager["next_url"]
    assert "model_page=3" in pager["next_url"]
    assert "model_q=gpt+vision" in pager["next_url"]
