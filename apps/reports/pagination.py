from django.core.paginator import Paginator


def paginate_section(request, queryset, *, parameter, per_page=25):
    page = Paginator(queryset, per_page).get_page(request.GET.get(parameter))

    def query_for(page_number):
        if not page_number:
            return ""
        params = request.GET.copy()
        params[parameter] = page_number
        return params.urlencode()

    return page, {
        "previous": query_for(page.previous_page_number()) if page.has_previous() else "",
        "next": query_for(page.next_page_number()) if page.has_next() else "",
    }
