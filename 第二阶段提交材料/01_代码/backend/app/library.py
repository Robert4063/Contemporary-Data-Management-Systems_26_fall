"""Compatibility router; resource logic lives in the corresponding modules."""
from fastapi import APIRouter
from .questions import router as questions
from .usual import router as usual

router = APIRouter()
router.include_router(questions)
router.include_router(usual)
