from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status

from src.api.dependencies import get_modify_page_use_case
from src.api.schemas import ModifyPageRequest, ModifyPageResponse
from src.application.use_cases.modify_page import ModifyPageUseCase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/modifications", tags=["Content Modifications"])


@router.post(
    "",
    response_model=ModifyPageResponse,
    status_code=status.HTTP_200_OK,
    summary="Modify WordPress page content with AI",
    description="""
Analyzes and transforms the editable text content of a WordPress page using the
configured LLM provider (default: Gemini).

**Protected elements** — never modified:
- Gutenberg block markers (`<!-- wp:* -->`)
- Shortcodes (`[contact-form-7 ...]`)
- Scripts, styles, iframes, forms

**Workflow:**
1. Resolve the identifier to a WordPress page/post ID.
2. Fetch the raw content using `context=edit`.
3. Save a backup of the original content.
4. Extract editable text segments.
5. Transform segments with the LLM.
6. Validate structural integrity.
7. If `dry_run=true`: return result without publishing.
8. If `dry_run=false`: publish to WordPress.
""",
)
async def modify_page(
    body: ModifyPageRequest,
    use_case: ModifyPageUseCase = Depends(get_modify_page_use_case),
) -> ModifyPageResponse:
    logger.info(
        "POST /modifications received",
        extra={
            "identifier": body.identifier,
            "dry_run": body.dry_run,
            "instructions_preview": body.instructions[:60],
        },
    )

    result = await use_case.execute(
        identifier=body.identifier,
        instructions=body.instructions,
        dry_run=body.dry_run,
    )

    return ModifyPageResponse.from_domain(result)
