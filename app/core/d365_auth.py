import time 
import requests
from app.core.config import settings

_token_cache={"access_token":None,"expires_at":0}

def get_d365_token() -> str:
    now=int(time.time())
    if _token_cache["access_token"] and now < (_token_cache["expires_at"]-60):
        return _token_cache["access_token"]
    token_url="https://fs.hiqelectronics.com/adfs/oauth2/token"
      #"https://sndfs.hiqelectronics.com/adfs/oauth2/token"
     # f"https://login.microsoftonline.com/{settings.AAD_TENANT_ID}/oauth2/token"
 
    # https://login.microsoftonline.com/{settings.AAD_TENANT_ID}/oauth2/v2.0/token"
    # data={
    #     "client_id":settings.D365_CLIENT_ID,
    #     "tenant_id":settings.D365_TENANT_ID,
    #     "grant_type": "client_credentials",
    #     "resource": settings.D365_RESOURCE,
    # }
    data = {
        "client_id":     settings.D365_CLIENT_ID,
        "client_secret": settings.D365_CLIENT_SECRET,  
        "grant_type":    "client_credentials",
        "resource":      settings.D365_RESOURCE,
    }

    resp=requests.post(token_url,data=data,timeout=settings.HTTP_TIMEOUT_SECONDS)
    resp.raise_for_status()

    js=resp.json()
    access_token=js["access_token"]
    expires_in = int(js.get("expires_in", "3600"))

    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = now + expires_in
    return access_token