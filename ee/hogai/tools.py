"""FOSS stub. Standard Max AI tools imported by products/conversations/backend/ai/mode_manager.py."""

from ee.hogai.tool import MaxTool


class ExecuteSQLTool(MaxTool):
    name = "execute_sql"


class ReadDataTool(MaxTool):
    name = "read_data"


class ReadTaxonomyTool(MaxTool):
    name = "read_taxonomy"


class SearchTool(MaxTool):
    name = "search"
