from __future__ import annotations

import asyncio
from io import BytesIO
from math import isqrt
from typing import Any

import httpx
import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from terratorch.registry import BACKBONE_REGISTRY


PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_TILER = "https://planetarycomputer.microsoft.com/api/data/v1"

S2_BANDS = ("B01","B02","B03","B04","B05","B06","B07","B08","B8A","B09","B11","B12")
S2_MEAN = np.asarray([1390.458,1503.317,1718.197,1853.910,2199.100,2779.975,2987.011,3083.234,3132.220,3162.988,2424.884,1857.648],dtype=np.float32)
S2_STD = np.asarray([2106.761,2141.107,2038.973,2134.138,2085.321,1889.926,1820.257,1871.918,1753.829,1797.379,1434.261,1334.311],dtype=np.float32)
S1_MEAN = np.asarray([-10.93,-17.329],dtype=np.float32)
S1_STD = np.asarray([4.391,4.459],dtype=np.float32)

app = FastAPI(title="HorusScan TerraMind Worker", version="0.1.0")
_model = None


class Request(BaseModel):
    task: str
    bbox: tuple[float,float,float,float]
    start: str
    end: str
    max_cloud: float = 20.0
    output_contract: dict[str,Any] | None = None


async def _search(collection: str, bbox, start: str, end: str, max_cloud: float | None, limit=5):
    payload: dict[str,Any] = {
        "collections":[collection],
        "bbox":list(bbox),
        "datetime":f"{start}/{end}",
        "limit":limit,
    }
    if max_cloud is not None:
        payload["query"]={"eo:cloud_cover":{"lte":max_cloud}}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response=await client.post(f"{PC_STAC}/search",json=payload)
        response.raise_for_status()
        items=response.json().get("features",[])
    if not items:
        raise RuntimeError(f"No {collection} scenes found.")
    return items


async def _read(item, collection: str, bbox, assets: tuple[str,...], shape=224):
    w,s,e,n=bbox
    url=f"{PC_TILER}/item/bbox/{w},{s},{e},{n}/{shape}x{shape}.npy"
    params=[
        ("collection",collection),("item",item["id"]),("asset_as_band","true"),
        ("return_mask","true"),("resampling","bilinear"),("unscale","false"),
    ]
    params.extend(("assets",asset) for asset in assets)
    async with httpx.AsyncClient(timeout=90.0) as client:
        response=await client.get(url,params=params)
        response.raise_for_status()
    arr=np.load(BytesIO(response.content),allow_pickle=False)
    data=arr[:len(assets)].astype(np.float32)
    mask=arr[-1]>0 if arr.shape[0]>len(assets) else np.all(np.isfinite(data),axis=0)
    data[:,~mask]=np.nan
    return data


def _load():
    global _model
    if _model is None:
        _model=BACKBONE_REGISTRY.build(
            "terramind_v1_tiny",
            pretrained=True,
            modalities=["S2L2A","S1RTC"],
            merge_method="mean",
        )
        _model=_model.to("cpu").eval()
    return _model


def _extract_tokens(output):
    value=output
    if isinstance(value,dict):
        value=list(value.values())[-1]
    if isinstance(value,(list,tuple)):
        value=value[-1]
    if isinstance(value,dict):
        value=list(value.values())[-1]
    if not torch.is_tensor(value):
        raise RuntimeError(f"Unsupported TerraMind output type: {type(value).__name__}")
    if value.ndim==4:
        value=value.flatten(2).transpose(1,2)
    if value.ndim!=3:
        raise RuntimeError(f"Unexpected TerraMind output shape: {tuple(value.shape)}")
    return value[0]


def _infer_sync(s2: np.ndarray, s1: np.ndarray, bbox):
    model=_load()
    for i in range(12):
        s2[i]=(s2[i]-S2_MEAN[i])/S2_STD[i]
    vv=np.maximum(s1[0],1e-8)
    vh=np.maximum(s1[1],1e-8)
    s1_db=np.stack([10*np.log10(vv),10*np.log10(vh)]).astype(np.float32)
    for i in range(2):
        s1_db[i]=(s1_db[i]-S1_MEAN[i])/S1_STD[i]

    inputs={
        "S2L2A":torch.from_numpy(np.nan_to_num(s2,nan=0.0)).unsqueeze(0),
        "S1RTC":torch.from_numpy(np.nan_to_num(s1_db,nan=0.0)).unsqueeze(0),
    }
    with torch.inference_mode():
        output=model(inputs)
    tokens=_extract_tokens(output)
    if tokens.shape[0]>1:
        side=isqrt(int(tokens.shape[0]))
        if side*side!=tokens.shape[0]:
            side=isqrt(int(tokens.shape[0]-1))
            tokens=tokens[-side*side:]
    else:
        raise RuntimeError("TerraMind returned too few tokens.")
    vectors=torch.nn.functional.normalize(tokens.float(),dim=-1)
    center=torch.nn.functional.normalize(vectors.mean(dim=0,keepdim=True),dim=-1)
    distance=(1-(vectors@center.T).squeeze(-1)).cpu().numpy()
    lo,hi=np.quantile(distance,[0.01,0.99])
    score=np.clip((distance-lo)/max(float(hi-lo),1e-6),0,1).reshape(side,side)

    w,s,e,n=bbox
    features=[]
    for row in range(side):
        for col in range(side):
            features.append({
                "type":"Feature",
                "geometry":{"type":"Point","coordinates":[w+((col+0.5)/side)*(e-w),n-((row+0.5)/side)*(n-s)]},
                "properties":{"anomaly_score":float(score[row,col])},
            })
    return {"type":"FeatureCollection","features":features}, int(tokens.shape[-1])


@app.get("/health")
async def health():
    return {"status":"ok","model_key":"terramind","model_name":"terramind_v1_tiny","device":"cpu","model_loaded":_model is not None}


@app.post("/infer")
async def infer(request: Request):
    try:
        s2_items=await _search("sentinel-2-l2a",request.bbox,request.start,request.end,request.max_cloud,5)
        s2_item=min(s2_items,key=lambda item:item.get("properties",{}).get("eo:cloud_cover",101.0))
        s1_items=await _search("sentinel-1-rtc",request.bbox,request.start,request.end,None,6)
        s1_item=next((item for item in s1_items if {"vv","vh"}.issubset(item.get("assets",{}))),s1_items[0])
        s2,s1=await asyncio.gather(
            _read(s2_item,"sentinel-2-l2a",request.bbox,S2_BANDS),
            _read(s1_item,"sentinel-1-rtc",request.bbox,("vv","vh")),
        )
        heatmap,dim=await asyncio.to_thread(_infer_sync,s2,s1,request.bbox)
    except Exception as exc:
        raise HTTPException(status_code=502,detail=f"TerraMind real inference failed: {type(exc).__name__}: {exc}") from exc

    return {
        "real_inference":True,
        "heatmap":heatmap,
        "model_metadata":{"model_key":"terramind","model_name":"terramind_v1_tiny","embedding_dim":dim,"device":"cpu"},
        "provenance":{
            "provider":"Microsoft Planetary Computer + TerraMind v1",
            "sentinel2_item":s2_item["id"],
            "sentinel1_item":s1_item["id"],
            "modalities":["S2L2A","S1RTC"],
            "method":"TerraMind multimodal embedding distance-to-context",
            "not_probability":True,
        },
    }


@app.get("/smoke")
async def smoke():
    result = await infer(
        Request(
            task="spatial_anomaly_heatmap",
            bbox=(31.20, 29.92, 31.22, 29.94),
            start="2026-04-01T00:00:00Z",
            end="2026-10-26T23:59:59Z",
            max_cloud=25.0,
        )
    )
    features=result.get("heatmap",{}).get("features",[])
    scores=[
        float(item.get("properties",{}).get("anomaly_score"))
        for item in features
        if isinstance(item.get("properties",{}).get("anomaly_score"),(int,float))
    ]
    return {
        "status":"completed",
        "real_inference":result.get("real_inference") is True,
        "model_metadata":result.get("model_metadata",{}),
        "feature_count":len(features),
        "score_min":min(scores) if scores else None,
        "score_max":max(scores) if scores else None,
        "provenance":result.get("provenance",{}),
    }
