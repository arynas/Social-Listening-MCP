"""An in-memory adapter, for tests and small demos."""
from slmcp.adapters.base import Adapter, filter_posts


class MemoryAdapter(Adapter):
    def __init__(self, posts, max_days=None):
        self.posts = list(posts)
        if max_days is not None:
            self.max_days = max_days

    def _search(self, keyword, start, end):
        return filter_posts(self.posts, keyword, start, end)
