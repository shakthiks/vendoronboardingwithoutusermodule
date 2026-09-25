from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    PROJECT_NAME: str = "Vendor Admin Portal"
    VERSION: str      = "1.0.0"
    DEBUG: bool       = False

    APP_ENV: str = "local"
    DEBUG: bool = True
    APP_NAME: str = "VendorADMINPortal"

    # ── VENDORPORTAL DB (DSN-based) ──────────────────────────────
    SQL_DSN:      str
    SQL_USERNAME: str
    SQL_PASSWORD: str
    VSQL_DSN:str
    # ── D365 SQL Server (VendTable + DirParty + PostalAddress) ───
    D365_DB_SERVER: str
    D365_DB_NAME:   str
    D365_DB_USER:   str
    D365_DB_PASSWORD: str 

    VENDOR_DB_SERVER: str = ""
    VENDOR_DB_NAME: str = ""
    VENDOR_DB_USER: str = ""
    VENDOR_DB_PASSWORD: str = ""
    DB_SCHEMA:str =""
    
    SECONDARY_DB_SERVER   :str =""
    SECONDARY_DB_NAME     :str = ""
    SECONDARY_DB_USER     :str = ""
    SECONDARY_DB_PASSWORD :str = ""
    SECONDARY_DB_SCHEMA   :str = ""


    # ── Email ───────────────────────────────────────────────
    MAIL_SERVER: str = ""
    MAIL_PORT: int = 587
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = ""

    # ── Frontend ────────────────────────────────────────────
    FRONTEND_BASE_URL: str = ""
    ONBOARDING_FRONTEND_URL: str = ""
    D365_VENDOR_CREATION_URL: str = ""
    D365_API_BASE_URL: str = ""
    D365_TENANT_ID: str = ""
    D365_CLIENT_ID: str = ""
    D365_CLIENT_SECRET: str = ""
    D365_RESOURCE: str = ""
    D365_RFQ_REPLY_URL: str = ""
    D365_SERVICE_GROUP: str = "HIQ_VendorCollaborationRFQServiceGroup"
    D365_SERVICE_NAME: str = "HIQ_VendorCollaborationRFQService"
    D365_RFQ_REPLY_URL: str = ""
    D365_BASE_URL: str = ""
    AAD_TENANT_ID: str = ""
    D365_PO_SERVICEURL: str = ""
    D365_VENDOR_RFQREPLY:str=""
    HTTP_TIMEOUT_SECONDS: int = 60
    @property
    def is_local(self) -> bool:                    # ← ADDED
        return self.APP_ENV == "local"

    @property
    def is_production(self) -> bool:               # ← ADDED
        return self.APP_ENV == "production"
    
    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    @property
    def vendorportal_conn_str(self) -> str:
        return (
            f"DSN={self.SQL_DSN};"
            f"UID={self.SQL_USERNAME};"
            f"PWD={self.SQL_PASSWORD};"
        )
    
    @property
    def d365_conn_str(self) -> str:
        return (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={self.D365_DB_SERVER};"
            f"DATABASE={self.D365_DB_NAME};"
            f"UID={self.D365_DB_USER};"
            f"PWD={self.D365_DB_PASSWORD};"
            f"TrustServerCertificate=yes;"
            f"Connection Timeout=15;"
        )



    class Config:
        env_file     = ".env"
        env_file_encoding = "utf-8"
        extra        = "ignore"   # ignores any extra vars in .env

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()