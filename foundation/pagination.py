DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


def parse_page_parameters(query_params):
    try:
        page = int(query_params.get("page", 1))
        page_size = int(query_params.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        raise ValueError("Page and page size must be positive whole numbers.") from None
    if page < 1:
        raise ValueError("Page must be at least 1.")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"Page size must be between 1 and {MAX_PAGE_SIZE}.")
    return page, page_size


def page_metadata(total_count, page, page_size):
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    if page > total_pages:
        raise ValueError(f"Page {page} is outside the available range (1–{total_pages}).")
    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "previous_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < total_pages else None,
    }
