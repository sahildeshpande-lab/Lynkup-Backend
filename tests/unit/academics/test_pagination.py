from common.pagination import OptionalPageSizeParams, paginate_or_all


def test_paginate_or_all_returns_all_when_page_params_omitted() -> None:
    items = [{"id": str(index)} for index in range(5)]
    result = paginate_or_all(items)
    assert result.items == items
    assert result.page == 1
    assert result.pageSize == 5
    assert result.totalItems == 5
    assert result.totalPages == 1


def test_paginate_or_all_slices_when_page_and_page_size_provided() -> None:
    items = [{"id": str(index)} for index in range(5)]
    result = paginate_or_all(items, page=2, page_size=2)
    assert [item["id"] for item in result.items] == ["2", "3"]
    assert result.page == 2
    assert result.pageSize == 2
    assert result.totalItems == 5
    assert result.totalPages == 3


def test_paginate_or_all_returns_all_when_only_one_param_provided() -> None:
    items = [{"id": str(index)} for index in range(4)]
    by_page = paginate_or_all(items, page=2, page_size=None)
    by_size = paginate_or_all(items, page=None, page_size=2)
    assert by_page.items == items
    assert by_size.items == items
    assert by_page.pageSize == 4
    assert by_size.pageSize == 4


def test_optional_page_size_params_default_page_and_all_items() -> None:
    params = OptionalPageSizeParams()
    assert params.page == 1
    assert params.pageSize is None
    sized = OptionalPageSizeParams(page=2, pageSize=10)
    assert sized.page == 2
    assert sized.pageSize == 10
