from fastapi import APIRouter, HTTPException, status, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import List, Optional
from sqlite_db import SQLiteHandler
import logging, uuid, os, re

sqlite_handler = SQLiteHandler()
router = APIRouter(
    prefix="/textfiles",
    tags=["textfiles"],
    responses={404: {"description": "Not found"}}
)

# ───────── Pydantic 모델 ─────────
class TextFileCreate(BaseModel):
    txt_title: str = Field(..., description="텍스트 파일 제목", min_length=1, max_length=100)
    txt_path:  str = Field(..., description="텍스트 파일 경로")
    type:      Optional[str] = Field(None, description="파일 확장자명")
    brain_id:  Optional[int] = Field(None, description="연결할 Brain ID")

class TextFileUpdate(BaseModel):
    txt_title: Optional[str] = Field(None, description="새 텍스트 파일 제목")
    txt_path:  Optional[str] = Field(None, description="새 텍스트 파일 경로")
    type:      Optional[str] = Field(None, description="파일 확장자명")
    brain_id:  Optional[int] = Field(None, description="새로운 Brain ID")

class TextFileResponse(BaseModel):
    txt_id:    int
    txt_title: str
    txt_path:  str
    txt_date:  str
    type:      Optional[str]
    brain_id:  Optional[int]

# ───────── 텍스트 파일 생성 ─────────
@router.post("/", response_model=TextFileResponse, status_code=status.HTTP_201_CREATED)
async def create_textfile(textfile_data: TextFileCreate):
    """새로운 텍스트 파일을 DB에 등록합니다."""
    if textfile_data.brain_id is not None:
        if not sqlite_handler.get_brain(textfile_data.brain_id):
            raise HTTPException(status_code=404, detail="Brain 엔티티를 찾을 수 없습니다")

    try:
        return sqlite_handler.create_textfile(
            txt_title=textfile_data.txt_title,
            txt_path=textfile_data.txt_path,
            type=textfile_data.type,
            brain_id=textfile_data.brain_id
        )
    except Exception as e:
        logging.error("텍스트 파일 생성 오류: %s", e)
        raise HTTPException(status_code=500, detail="내부 서버 오류")

# ───────── 텍스트 파일 단건 조회 ─────────
@router.get("/{txt_id}", response_model=TextFileResponse)
async def get_textfile(txt_id: int):
    """텍스트 파일 ID로 단일 조회"""
    textfile = sqlite_handler.get_textfile(txt_id)
    if not textfile:
        raise HTTPException(status_code=404, detail="텍스트 파일을 찾을 수 없습니다")
    return textfile

# ───────── 텍스트 파일 수정 ─────────
@router.put("/{txt_id}", response_model=TextFileResponse)
async def update_textfile(txt_id: int, textfile_data: TextFileUpdate):
    """텍스트 파일의 제목, 경로, brain_id 등을 수정"""
    if not sqlite_handler.get_textfile(txt_id):
        raise HTTPException(status_code=404, detail="텍스트 파일을 찾을 수 없습니다")

    if textfile_data.brain_id is not None:
        if not sqlite_handler.get_brain(textfile_data.brain_id):
            raise HTTPException(status_code=404, detail="Brain 엔티티를 찾을 수 없습니다")

    try:
        updated = sqlite_handler.update_textfile(
            txt_id=txt_id,
            txt_title=textfile_data.txt_title,
            txt_path=textfile_data.txt_path,
            type=textfile_data.type,
            brain_id=textfile_data.brain_id
        )
        if not updated:
            raise HTTPException(status_code=400, detail="업데이트할 정보가 없습니다")
        return sqlite_handler.get_textfile(txt_id)
    except Exception as e:
        logging.error("텍스트 파일 업데이트 오류: %s", e)
        raise HTTPException(status_code=500, detail="내부 서버 오류")

# ───────── 텍스트 파일 삭제 ─────────
@router.delete("/{txt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_textfile(txt_id: int):
    """텍스트 파일 삭제"""
    if not sqlite_handler.delete_textfile(txt_id):
        raise HTTPException(status_code=404, detail="텍스트 파일을 찾을 수 없습니다")

# ───────── Brain 기준 텍스트 파일 목록 조회 ─────────
@router.get("/brain/{brain_id}", response_model=List[TextFileResponse])
async def get_textfiles_by_brain(brain_id: int):
    """특정 Brain에 속한 텍스트 파일 목록 전체 조회"""
    try:
        return sqlite_handler.get_textfiles_by_brain(brain_id)
    except Exception as e:
        logging.error("텍스트 파일 조회 오류: %s", e)
        raise HTTPException(status_code=500, detail="서버 오류")

# ───────── 텍스트 파일 업로드 ─────────
UPLOAD_TXT_DIR = "uploaded_txts"
os.makedirs(UPLOAD_TXT_DIR, exist_ok=True)

def sanitize_filename(name):
    """파일명에서 위험한 문자 제거"""
    return re.sub(r'[^\w\-_\. ]', '_', name)

@router.post("/upload-txt", response_model=List[TextFileResponse])
async def upload_textfiles(
    files: List[UploadFile] = File(...),
    brain_id: Optional[int] = Form(None)
):
    """TXT 파일 업로드 후 DB에 등록"""
    uploaded_textfiles = []

    if brain_id is not None and not sqlite_handler.get_brain(brain_id):
        raise HTTPException(status_code=404, detail="해당 Brain이 존재하지 않습니다.")

    for file in files:
        try:
            ext = os.path.splitext(file.filename)[1].lower()
            if ext != ".txt":
                continue

            safe_name = sanitize_filename(file.filename)
            unique_name = f"{uuid.uuid4().hex}_{safe_name}"
            file_path = os.path.join(UPLOAD_TXT_DIR, unique_name)

            with open(file_path, "wb") as f:
                f.write(await file.read())

            created = sqlite_handler.create_textfile(
                txt_title=safe_name,
                txt_path=file_path,
                type="txt",
                brain_id=brain_id
            )
            uploaded_textfiles.append(created)
        except Exception as e:
            logging.error("TXT 업로드 실패 (%s): %s", file.filename, e)

    return uploaded_textfiles
