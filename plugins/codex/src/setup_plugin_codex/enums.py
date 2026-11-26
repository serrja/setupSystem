from enum import StrEnum


class ClientAction(StrEnum):
    LOGIN = "login"
    LOGOUT = "logout"
    STATUS = "status"
    CONFIGURE_MCP = "configure-mcp"
    MCP = "mcp"
    SKILLS = "skills"
