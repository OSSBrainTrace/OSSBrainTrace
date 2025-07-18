from fastapi import APIRouter, HTTPException
from models.request_models import ProcessTextRequest, AnswerRequest, GraphResponse
from services import ai_service, embedding_service
from neo4j_db.Neo4jHandler import Neo4jHandler
import logging
from sqlite_db import SQLiteHandler
from exceptions.custom_exceptions import Neo4jException,AppException, GraphDataNotFoundException, QdrantException
from examples.error_examples import ErrorExamples



router = APIRouter(
    prefix="/brainGraph",
    tags=["brainGraph"],
    responses={404: {"description": "Not found"}}
)

@router.get(
    "/getNodeEdge/{brain_id}",
    response_model=GraphResponse,
    summary="브레인의 그래프 데이터 조회",
    description="특정 브레인의 모든 노드와 엣지(관계) 정보를 반환합니다.",
     responses={
        404: ErrorExamples[40401],
        500: ErrorExamples[50001]
    }
    
)
async def get_brain_graph(brain_id: str):
    """
    특정 브레인의 그래프 데이터를 반환합니다:
    
    - **brain_id**: 그래프를 조회할 브레인 ID
    
    반환값:
    - **nodes**: 노드 목록 (각 노드는 name 속성을 가짐)
    - **links**: 엣지 목록 (각 엣지는 source, target, relation 속성을 가짐)
    """
    logging.info(f"getNodeEdge 엔드포인트 호출됨 - brain_id: {brain_id}")
    try:
        neo4j_handler = Neo4jHandler()
        logging.info("Neo4j 핸들러 생성됨")
        
        graph_data = neo4j_handler.get_brain_graph(brain_id)
        logging.info(f"Neo4j에서 받은 데이터: nodes={len(graph_data['nodes'])}, links={len(graph_data['links'])}")
        
        # if not graph_data['nodes'] and not graph_data['links']:
        #     logging.warning(f"brain_id {brain_id}에 대한 데이터가 없습니다")
        #     raise GraphDataNotFoundException(brain_id)
        
        if not graph_data['nodes'] and not graph_data['links']:
            logging.warning(f"brain_id {brain_id}에 대한 데이터가 없습니다")

        return graph_data
    except AppException as ae:
            raise ae
    except Exception as e:
        logging.error("그래프 데이터 조회 오류: %s", str(e))
        raise Neo4jException(message=str(e))
        

@router.post("/process_text", 
    summary="텍스트 처리 및 그래프 생성",
    description="입력된 텍스트에서 노드와 엣지를 추출하여 Neo4j에 저장하고, 노드 정보를 벡터 DB에 임베딩합니다.",
    response_description="처리된 노드와 엣지 정보를 반환합니다.",
    responses={
        500: ErrorExamples[50001]
    }
    )
async def process_text_endpoint(request_data: ProcessTextRequest):
    """
    텍스트를 받아 노드/엣지 추출, Neo4j 저장, 벡터 DB 임베딩까지 전체 파이프라인 실행
    """
    text = request_data.text
    source_id = request_data.source_id
    brain_id = request_data.brain_id
    
    if not text:
        raise HTTPException(status_code=400, detail="text 파라미터가 필요합니다.")
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 파라미터가 필요합니다.")
    if not brain_id:
        raise HTTPException(status_code=400, detail="brain_id 파라미터가 필요합니다.")
    
    logging.info("사용자 입력 텍스트: %s, source_id: %s, brain_id: %s", text, source_id, brain_id)
    
    # Step 1: 텍스트에서 노드/엣지 추출 (AI 서비스)
    nodes, edges = ai_service.extract_graph_components(text, source_id)
    logging.info("추출된 노드: %s", nodes)
    logging.info("추출된 엣지: %s", edges)

    # Step 2: Neo4j에 노드와 엣지 저장 
    neo4j_handler = Neo4jHandler()
    neo4j_handler.insert_nodes_and_edges(nodes, edges, brain_id)
    logging.info("Neo4j에 노드와 엣지 삽입 완료")

    # Step 3: 노드 정보를 벡터 DB에 임베딩
    # 컬렉션이 없으면 초기화
    if not embedding_service.is_index_ready(brain_id):
        embedding_service.initialize_collection(brain_id)
    
    # 노드 정보 임베딩 및 저장
    embeddings = embedding_service.update_index_and_get_embeddings(nodes, brain_id)
    logging.info("벡터 DB에 노드 임베딩 저장 완료")

    return {
        "message": "텍스트 처리 완료, 그래프(노드와 엣지)가 생성되었고 벡터 DB에 임베딩되었습니다.",
        "nodes": nodes,
        "edges": edges
    }

@router.post("/answer",
    summary="질문에 대한 답변 생성",
    description="사용자의 질문에 대해 Neo4j에서 관련 정보를 찾아 답변을 생성합니다.",
    response_description="생성된 답변을 반환합니다.",
    responses={
    500: {
        "description": "서버 내부 오류 (50001, 50002 등)",
        "content": {
            "application/json": {
                "examples": {
                    "50001": ErrorExamples[50001]["content"]["application/json"],
                    "50002": ErrorExamples[50002]["content"]["application/json"]
                }
            }
        }
    }
}
)
async def answer_endpoint(request_data: AnswerRequest):
    """
    사용자 질문을 받아 임베딩을 통해 유사한 노드를 찾고, 
    해당 노드들의 2단계 깊이 스키마를 추출 후 LLM을 이용해 최종 답변 생성
    """
    question = request_data.question
    brain_id = request_data.brain_id  # 요청에서 brain_id 받아오기
    
    if not question:
        raise HTTPException(status_code=400, detail="question 파라미터가 필요합니다.")
    if not brain_id:
        raise HTTPException(status_code=400, detail="brain_id 파라미터가 필요합니다.")
    
    logging.info("질문 접수: %s, brain_id: %s", question, brain_id)
    
    try:
        # 사용자 질문 저장
        db_handler = SQLiteHandler()
        chat_id = db_handler.save_chat(False, question, brain_id)
        
        # Step 1: 컬렉션이 없으면 초기화
        if not embedding_service.is_index_ready(brain_id):
            embedding_service.initialize_collection(brain_id)
            logging.info("Qdrant 컬렉션 초기화 완료: %s", brain_id)
        
        # Step 2: 질문 임베딩 계산
        question_embedding = embedding_service.encode_text(question)
        
        # Step 3: 임베딩을 통해 유사한 노드 검색
        similar_nodes = embedding_service.search_similar_nodes(embedding=question_embedding, brain_id=brain_id)
        if not similar_nodes:
            raise QdrantException("질문과 유사한 노드를 찾지 못했습니다.")
        
        # 노드 이름만 추출
        similar_node_names = [node["name"] for node in similar_nodes]
        logging.info("sim node name: %s", similar_node_names)
        logging.info("sim node score: %s", [f"{node['name']}:{node['score']:.2f}" for node in similar_nodes])
        
        # Step 4: 유사한 노드들의 2단계 깊이 스키마 조회
        neo4j_handler = Neo4jHandler()
        result = neo4j_handler.query_schema_by_node_names(similar_node_names, brain_id)
        if not result:
            raise Neo4jException("스키마 조회 결과가 없습니다.")
            
        logging.info("### Neo4j 조회 결과 전체: %s", result)
        
        # 결과를 즉시 처리
        nodes_result = result.get("nodes", [])
        related_nodes_result = result.get("relatedNodes", [])
        relationships_result = result.get("relationships", [])
        
        logging.info("Neo4j search result: nodes=%d, related_nodes=%d, relationships=%d", 
                   len(nodes_result), len(related_nodes_result), len(relationships_result))
        
        # Step 5: 스키마 간결화 및 텍스트 구성
        raw_schema_text = ai_service.generate_schema_text(nodes_result, related_nodes_result, relationships_result)
        
        # Step 6: LLM을을 사용해 최종 답변 생성
        final_answer = ai_service.generate_answer(raw_schema_text, question)
        referenced_nodes = ai_service.extract_referenced_nodes(final_answer)
        final_answer = final_answer.split("EOF")[0].strip()
        
        # referenced_nodes 내용을 텍스트로 final_answer 뒤에 추가
        if referenced_nodes:
            nodes_text = "\n\n[참고된 노드 목록]\n" + "\n".join(f"- {node}" for node in referenced_nodes)
            final_answer += nodes_text
            
        # AI 답변 저장
        # AI 답변 저장 및 chat_id 획득
        chat_id = db_handler.save_chat(True, final_answer, brain_id, referenced_nodes)

        return {
            "answer": final_answer,
            "referenced_nodes": referenced_nodes,
            "chat_id": chat_id   # ✅ 반드시 포함!
        }
    except Exception as e:
        logging.error("answer 오류: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/getSourceIds",
    summary="노드의 모든 source_id와 제목을 조회",
    description="특정 노드의 descriptions 배열에서 모든 source_id를 추출하여 반환합니다.",
    response_description="source_id와 title을 포함하는 객체 리스트를 반환합니다.",
    responses={
    500: ErrorExamples[50002]
    }
)
async def get_source_ids(node_name: str, brain_id: str):
    """
    노드의 모든 source_id와 제목을 반환합니다:
    
    - **node_name**: 조회할 노드의 이름
    - **brain_id**: 브레인 ID
    
    반환값:
    - **sources**: source_id와 title을 포함하는 객체 리스트
    """
    logging.info(f"getSourceIds 엔드포인트 호출됨 - node_name: {node_name}, brain_id: {brain_id}")
    try:
        neo4j_handler = Neo4jHandler()
        db = SQLiteHandler()
        logging.info("Neo4j 핸들러 생성됨")
        
        # Neo4j에서 노드의 descriptions 배열 조회
        descriptions = neo4j_handler.get_node_descriptions(node_name, brain_id)
        if not descriptions:
            return {"sources": []}
            
        # descriptions 배열에서 모든 source_id 추출
        seen_ids = set()  # 중복 제거를 위해 set 사용
        sources = []
        
        for desc in descriptions:
            if "source_id" in desc:
                source_id = desc["source_id"]
                if source_id not in seen_ids:
                    seen_ids.add(source_id)
                    
                    # PDF와 TextFile 테이블에서 모두 조회
                    pdf = db.get_pdf(int(source_id))
                    textfile = db.get_textfile(int(source_id))
                    
                    title = None
                    if pdf:
                        title = pdf['pdf_title']
                    elif textfile:
                        title = textfile['txt_title']
                    
                    if title:
                        sources.append({
                            "id": source_id,
                            "title": title
                        })
        
        logging.info(f"추출된 sources: {sources}")
        return {"sources": sources}
        
    except Exception as e:
        logging.error("source_id 조회 오류: %s", str(e))
        raise HTTPException(status_code=500, detail=f"source_id 조회 중 오류가 발생했습니다: {str(e)}")

@router.get("/getNodesBySourceId",
    summary="source_id로 노드 조회",
    description="특정 source_id가 descriptions에 포함된 모든 노드의 이름을 반환합니다.",
    response_description="노드 이름 목록을 반환합니다.")
async def get_nodes_by_source_id(source_id: str, brain_id: str):
    """
    source_id로 노드를 조회합니다:
    
    - **source_id**: 찾을 source_id
    - **brain_id**: 브레인 ID
    
    반환값:
    - **nodes**: 노드 이름 목록
    """
    logging.info(f"getNodesBySourceId 엔드포인트 호출됨 - source_id: {source_id}, brain_id: {brain_id}")
    try:
        neo4j_handler = Neo4jHandler()
        logging.info("Neo4j 핸들러 생성됨")
        
        # Neo4j에서 source_id로 노드 조회
        node_names = neo4j_handler.get_nodes_by_source_id(source_id, brain_id)
        logging.info(f"조회된 노드 이름: {node_names}")
        
        return {"nodes": node_names}
        
    except Exception as e:
        logging.error("노드 조회 오류: %s", str(e))
        raise HTTPException(status_code=500, detail=f"노드 조회 중 오류가 발생했습니다: {str(e)}")

@router.get("/getSourceDataMetrics/{brain_id}",
    summary="브레인의 소스별 데이터 메트릭 조회",
    description="특정 브레인의 모든 소스에 대한 텍스트 양과 그래프 데이터 양을 계산하여 반환합니다.",
    response_description="소스별 텍스트 양과 그래프 데이터 양 정보를 반환합니다.",
    responses={
        404: ErrorExamples[40401],
        500: ErrorExamples[50001]
    }
)
async def get_source_data_metrics(brain_id: str):
    """
    특정 브레인의 모든 소스에 대한 데이터 메트릭을 반환합니다:
    
    - **brain_id**: 메트릭을 조회할 브레인 ID
    
    반환값:
    - **total_text_length**: 전체 텍스트 길이
    - **total_nodes**: 전체 노드 수
    - **total_edges**: 전체 엣지 수
    - **source_metrics**: 소스별 상세 메트릭
    """
    logging.info(f"getSourceDataMetrics 엔드포인트 호출됨 - brain_id: {brain_id}")
    try:
        neo4j_handler = Neo4jHandler()
        db_handler = SQLiteHandler()
        
        # 1. Neo4j에서 그래프 데이터 조회
        graph_data = neo4j_handler.get_brain_graph(brain_id)
        total_nodes = len(graph_data.get('nodes', []))
        total_edges = len(graph_data.get('links', []))
        
        # 2. SQLite에서 소스별 텍스트 길이 계산
        source_metrics = []
        total_text_length = 0
        
        # PDF 소스들 조회
        pdfs = db_handler.get_pdfs_by_brain(brain_id)
        for pdf in pdfs:
            try:
                # PDF 파일에서 텍스트 추출 (간단한 추정)
                # 실제로는 PDF 파싱이 필요하지만, 여기서는 파일 크기로 추정
                import os
                if os.path.exists(pdf['pdf_path']):
                    file_size = os.path.getsize(pdf['pdf_path'])
                    # PDF 파일 크기를 텍스트 길이로 추정 (대략적인 계산)
                    estimated_text_length = int(file_size * 0.1)  # PDF의 약 10%가 텍스트라고 가정
                else:
                    estimated_text_length = 0
                
                # 이 PDF에서 생성된 노드 수 계산
                pdf_nodes = neo4j_handler.get_nodes_by_source_id(pdf['pdf_id'], brain_id)
                pdf_edges = neo4j_handler.get_edges_by_source_id(pdf['pdf_id'], brain_id)
                
                source_metrics.append({
                    "source_id": pdf['pdf_id'],
                    "source_type": "pdf",
                    "title": pdf['pdf_title'],
                    "text_length": estimated_text_length,
                    "nodes_count": len(pdf_nodes),
                    "edges_count": len(pdf_edges)
                })
                
                total_text_length += estimated_text_length
                
            except Exception as e:
                logging.error(f"PDF 메트릭 계산 오류 (ID: {pdf['pdf_id']}): {str(e)}")
        
        # TXT 소스들 조회
        txts = db_handler.get_textfiles_by_brain(brain_id)
        for txt in txts:
            try:
                # TXT 파일에서 실제 텍스트 길이 계산
                import os
                if os.path.exists(txt['txt_path']):
                    with open(txt['txt_path'], 'r', encoding='utf-8') as f:
                        text_content = f.read()
                        text_length = len(text_content)
                else:
                    text_length = 0
                
                # 이 TXT에서 생성된 노드 수 계산
                txt_nodes = neo4j_handler.get_nodes_by_source_id(txt['txt_id'], brain_id)
                txt_edges = neo4j_handler.get_edges_by_source_id(txt['txt_id'], brain_id)
                
                source_metrics.append({
                    "source_id": txt['txt_id'],
                    "source_type": "txt",
                    "title": txt['txt_title'],
                    "text_length": text_length,
                    "nodes_count": len(txt_nodes),
                    "edges_count": len(txt_edges)
                })
                
                total_text_length += text_length
                
            except Exception as e:
                logging.error(f"TXT 메트릭 계산 오류 (ID: {txt['txt_id']}): {str(e)}")
        
        # MEMO 소스들 조회
        memos = db_handler.get_memos_by_brain(brain_id, is_source=True)
        for memo in memos:
            try:
                # 메모 텍스트 길이 계산
                text_length = len(memo['memo_text'] or '')
                
                # 이 메모에서 생성된 노드 수 계산
                memo_nodes = neo4j_handler.get_nodes_by_source_id(memo['memo_id'], brain_id)
                memo_edges = neo4j_handler.get_edges_by_source_id(memo['memo_id'], brain_id)
                
                source_metrics.append({
                    "source_id": memo['memo_id'],
                    "source_type": "memo",
                    "title": memo['memo_title'],
                    "text_length": text_length,
                    "nodes_count": len(memo_nodes),
                    "edges_count": len(memo_edges)
                })
                
                total_text_length += text_length
                
            except Exception as e:
                logging.error(f"MEMO 메트릭 계산 오류 (ID: {memo['memo_id']}): {str(e)}")
        
        return {
            "total_text_length": total_text_length,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "source_metrics": source_metrics
        }
        
    except AppException as ae:
        raise ae
    except Exception as e:
        logging.error("소스 데이터 메트릭 조회 오류: %s", str(e))
        raise Neo4jException(message=str(e))

@router.get("/sourceCount/{brain_id}", summary="브레인별 전체 소스 개수 조회", description="특정 브레인에 속한 PDF, TXT, MD, MEMO 소스의 개수를 반환합니다.")
async def get_source_count(brain_id: int):
    """
    해당 brain_id에 속한 모든 소스(PDF, TXT, MD, MEMO) 개수를 반환합니다.
    is_source가 true인 메모만 소스로 계산합니다.
    """
    db = SQLiteHandler()
    try:
        pdfs = db.get_pdfs_by_brain(brain_id)
        txts = db.get_textfiles_by_brain(brain_id)
        mds = db.get_mds_by_brain(brain_id)
        memos = db.get_memos_by_brain(brain_id, is_source=True)  # is_source가 True인 메모만 조회
        total_count = len(pdfs) + len(txts) + len(mds) + len(memos)
        return {
            "pdf_count": len(pdfs),
            "txt_count": len(txts),
            "md_count": len(mds),
            "memo_count": len(memos),
            "total_count": total_count
        }
    except Exception as e:
        raise HTTPException(500, f"소스 개수 조회 중 오류: {str(e)}")