"""
Junction Template API
Provides endpoints for saving, loading, and managing junction templates.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/junction-templates", tags=["junction-templates"])

# 模板存储路径
TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "data" / "junction_templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


class JunctionTemplateCreate(BaseModel):
    """创建路口模板的请求体"""
    junction: Dict[str, Any]
    compositions: List[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]] = None


class JunctionTemplateUpdate(BaseModel):
    """更新路口模板的请求体"""
    junction: Optional[Dict[str, Any]] = None
    compositions: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


class JunctionTemplateResponse(BaseModel):
    """路口模板响应"""
    template_id: str
    filename: str
    junction: Dict[str, Any]
    compositions: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str


@router.post("", response_model=JunctionTemplateResponse)
async def create_junction_template(template: JunctionTemplateCreate):
    """
    保存新的路口模板
    
    - **junction**: 路口标注数据
    - **compositions**: 路口组合数据（骨架线、Bezier面片等）
    - **metadata**: 可选的元数据（版本、描述等）
    """
    # 生成模板ID
    template_id = template.junction.get("id", f"template_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    filename = f"{template_id}.json"
    filepath = TEMPLATES_DIR / filename
    
    # 构建模板数据
    template_data = {
        "template_id": template_id,
        "junction": template.junction,
        "compositions": template.compositions,
        "metadata": template.metadata or {},
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    
    # 保存到文件
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(template_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save template: {str(e)}")
    
    return JunctionTemplateResponse(**template_data, filename=filename)


@router.get("", response_model=List[Dict[str, Any]])
async def list_junction_templates():
    """
    列出所有路口模板
    
    返回模板的基本信息（ID、创建时间、元数据等）
    """
    templates = []
    
    for filepath in TEMPLATES_DIR.glob("*.json"):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                templates.append({
                    "template_id": data.get("template_id"),
                    "filename": filepath.name,
                    "metadata": data.get("metadata", {}),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                })
        except Exception as e:
            # 跳过损坏的文件
            continue
    
    # 按创建时间倒序排序
    templates.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return templates


@router.get("/{template_id}", response_model=JunctionTemplateResponse)
async def get_junction_template(template_id: str):
    """
    获取指定路口模板的完整数据
    
    - **template_id**: 模板ID
    """
    filename = f"{template_id}.json" if not template_id.endswith(".json") else template_id
    filepath = TEMPLATES_DIR / filename
    
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read template: {str(e)}")
    
    return JunctionTemplateResponse(
        template_id=data.get("template_id"),
        filename=filename,
        junction=data.get("junction", {}),
        compositions=data.get("compositions", []),
        metadata=data.get("metadata", {}),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
    )


@router.put("/{template_id}", response_model=JunctionTemplateResponse)
async def update_junction_template(template_id: str, update: JunctionTemplateUpdate):
    """
    更新路口模板
    
    - **template_id**: 模板ID
    - **junction**: 更新的路口数据（可选）
    - **compositions**: 更新的组合数据（可选）
    - **metadata**: 更新的元数据（可选）
    """
    filename = f"{template_id}.json" if not template_id.endswith(".json") else template_id
    filepath = TEMPLATES_DIR / filename
    
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    
    # 读取现有数据
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read template: {str(e)}")
    
    # 更新字段
    if update.junction is not None:
        data["junction"] = update.junction
    if update.compositions is not None:
        data["compositions"] = update.compositions
    if update.metadata is not None:
        data["metadata"] = {**data.get("metadata", {}), **update.metadata}
    
    data["updated_at"] = datetime.now().isoformat()
    
    # 保存更新
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update template: {str(e)}")
    
    return JunctionTemplateResponse(
        template_id=data.get("template_id"),
        filename=filename,
        junction=data.get("junction", {}),
        compositions=data.get("compositions", []),
        metadata=data.get("metadata", {}),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
    )


@router.delete("/{template_id}")
async def delete_junction_template(template_id: str):
    """
    删除路口模板
    
    - **template_id**: 模板ID
    """
    filename = f"{template_id}.json" if not template_id.endswith(".json") else template_id
    filepath = TEMPLATES_DIR / filename
    
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    
    try:
        filepath.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete template: {str(e)}")
    
    return {"status": "ok", "message": f"Template deleted: {template_id}"}


@router.get("/{template_id}/download")
async def download_junction_template(template_id: str):
    """
    下载路口模板JSON文件
    
    - **template_id**: 模板ID
    """
    from fastapi.responses import FileResponse
    
    filename = f"{template_id}.json" if not template_id.endswith(".json") else template_id
    filepath = TEMPLATES_DIR / filename
    
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    
    return FileResponse(
        path=str(filepath),
        filename=filename,
        media_type="application/json",
    )
