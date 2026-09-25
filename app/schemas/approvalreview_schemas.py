from pydantic import BaseModel
from typing import Optional

class ApprovalListPageRequest(BaseModel):
    ProspectId: str 



class ApprovalReviewUpdateRequest(BaseModel):

    status: str
    ProspectId:str
    Comments:Optional[str] = None
    ToEmail:Optional[str] = None
    


    