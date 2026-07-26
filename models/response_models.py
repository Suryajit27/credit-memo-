from pydantic import BaseModel, Field
from typing import List, Optional

class DocumentResult(BaseModel):
    fileName: str = Field(..., description="The name of the document, optionally including its relative path within the ZIP.")
    documentType: Optional[str] = Field(None, description="The classified document type.")
    confidence: Optional[float] = Field(None, description="The confidence score of the classification.")
    status: Optional[str] = Field(None, description="The status of the processing, e.g., 'Success' or 'Failed'.")
    error: Optional[str] = Field(None, description="The error message if processing failed.")

class Summary(BaseModel):
    totalDocuments: int = Field(..., description="Total number of supported documents found in the ZIP.")
    successful: int = Field(..., description="Number of documents successfully classified.")
    failed: int = Field(..., description="Number of documents that failed classification.")

class ClassificationResponse(BaseModel):
    documents: List[DocumentResult]
    summary: Summary

class UploadDocumentResult(BaseModel):
    originalPath: str = Field(..., description="Original path or filename in zip")
    blobName: str = Field(..., description="Full path / name of the uploaded blob in storage")
    documentType: str = Field(..., description="Assigned document type name")
    confidence: Optional[float] = Field(None, description="Classification confidence")
    status: str = Field(..., description="Status of upload / classification")
    uploadedAt: str = Field(..., description="ISO timestamp of upload")

class UploadResponse(BaseModel):
    requestId: str = Field(..., description="Unique request ID tracking this batch upload")
    uploadedDocument: UploadDocumentResult
    message: str = Field(..., description="Status message")

class IndexerStatusResponse(BaseModel):
    requestId: str = Field(..., description="Request ID associated with the indexing job")
    indexingStatus: str = Field(..., description="Status: 'pending', 'running', 'succeeded', or 'failed'")
    indexedCount: int = Field(..., description="Number of items indexed so far")
    totalCount: int = Field(..., description="Total items expected in this batch")
    indexingErrors: List[str] = Field(default_factory=list, description="List of indexing error messages")
    indexingStartedAt: Optional[str] = Field(None, description="ISO timestamp when indexing started")
    indexingCompletedAt: Optional[str] = Field(None, description="ISO timestamp when indexing finished")

class SearchResultItem(BaseModel):
    content: str = Field(..., description="Text snippet of the chunk matching the query")
    documentType: Optional[str] = Field(None, description="Classified document type")
    blobName: Optional[str] = Field(None, description="Source blob path")
    doc_id: Optional[str] = Field(None, description="Document identifier")
    doc_name: Optional[str] = Field(None, description="Original document file name")
    page: Optional[int] = Field(None, description="Document page number")
    chunk_id: Optional[str] = Field(None, description="Unique chunk ID")
    confidence: Optional[float] = Field(None, description="Classification confidence")
    score: Optional[float] = Field(None, description="Hybrid search similarity/relevance score")
    requestId: Optional[str] = Field(None, description="Associated request ID")

class SearchResponse(BaseModel):
    results: List[SearchResultItem]
    totalCount: int = Field(..., description="Number of results returned")
    searchMode: str = Field(..., description="Search execution mode: hybrid, vector, or keyword")
    vectorUsed: bool = Field(..., description="Whether vector similarity was used for this query")
    fallbackUsed: bool = Field(False, description="Whether unscoped fallback search was used")
    warning: Optional[str] = Field(None, description="Optional warning message when fallback broadens scope")

# --- Credit Memo Data Models ---

class CitationItem(BaseModel):
    doc_id: str = Field(..., description="Source document ID")
    doc_name: str = Field(..., description="Source document name")
    page: Optional[int] = Field(None, description="Page number")
    chunk_id: Optional[str] = Field(None, description="Chunk identifier")
    retrieved_at: Optional[str] = Field(None, description="ISO timestamp of retrieval")

class MemoSection(BaseModel):
    name: str = Field(..., description="Section title from taxonomy")
    status: str = Field("drafted", description="Section status: drafted, approved, regenerating, manual_escalation")
    content: str = Field("", description="Drafted section content in Markdown format including inline footnotes")
    citations: List[CitationItem] = Field(default_factory=list, description="Citations used in this section")
    rationale: Optional[str] = Field(None, description="Analysis rationale for why section is included/excluded")
    regen_count: int = Field(0, description="Number of times this section was regenerated (max 2)")
    reviewer_notes: Optional[str] = Field(None, description="Feedback left by human reviewer")

class CreditMemoRecord(BaseModel):
    id: str = Field(..., description="Record ID matching requestId")
    requestId: str = Field(..., description="Associated request ID")
    threadId: Optional[str] = Field(None, description="Azure AI Agent Service thread ID")
    version: int = Field(1, description="Version number of the credit memo")
    status: str = Field("in_progress", description="Status: in_progress, in_review, final")
    sections: List[MemoSection] = Field(default_factory=list, description="List of memo sections")
    createdBy: Optional[str] = Field("OrchestratorAgent", description="Creator of the memo")
    approvedBy: Optional[str] = Field(None, description="User who gave final approval")
    approvedAt: Optional[str] = Field(None, description="Timestamp of final approval")
    blobPath: Optional[str] = Field(None, description="Blob storage path of final markdown memo")
    createdAt: Optional[str] = Field(None, description="Creation ISO timestamp")
    updatedAt: Optional[str] = Field(None, description="Last update ISO timestamp")

class MemoStartResponse(BaseModel):
    requestId: str
    memo: CreditMemoRecord
    message: str

class RegenerateSectionRequest(BaseModel):
    reviewer_notes: Optional[str] = Field(None, description="Optional notes for redrafting this section")

class FinalizeMemoResponse(BaseModel):
    requestId: str
    blobPath: str
    status: str
    approvedBy: str
    approvedAt: str
    message: str
