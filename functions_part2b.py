"""
생산 계획 하이브리드 시스템 - Part 2B
6단계: Python 최종 검증
"""

def step6_validate_ai_strategy(ai_strategy, constraint_info, capa_status, plan_df, target_line):
    """
    6단계: AI 전략을 Python으로 최종 검증
    
    검증 항목:
    1. 품목 존재 여부
    2. 누적 납기 여유 확인
    3. PLT 단위 준수
    4. 물리 제약 (T6/A2XX/전용모델)
    5. 목적지 CAPA 확인
    6. 가동일 확인
    
    Args:
        ai_strategy (dict): AI가 제안한 전략 {"strategy": "...", "moves": [...]}
        constraint_info (list): 물리 제약 정보
        capa_status (dict): 목적지 CAPA 현황
        plan_df (DataFrame): 생산 계획 데이터
        target_line (str): 대상 라인
    
    Returns:
        tuple: (validated_moves, violations)
            - validated_moves (list): 검증 통과한 조치 목록
            - violations (list): 위반 사항 및 조정 내역
    """
    
    if not ai_strategy or 'moves' not in ai_strategy:
        return [], ["❌ AI 전략 형식 오류: 'moves' 키가 없습니다."]
    
    validated_moves = []
    violations = []
    
    for idx, move in enumerate(ai_strategy['moves'], 1):
        item_name = move.get('item')
        qty = move.get('qty', 0)
        to_loc = move.get('to', '')
        reason = move.get('reason', '미지정')
        
        # ========== [검증 1] 품목 존재 확인 ==========
        item = next((x for x in constraint_info if x['name'] == item_name), None)
        if not item:
            violations.append(f"❌ [{idx}] {item_name}: 이동 가능 품목 목록에 없음")
            continue
        
        # ========== [검증 2] 누적 납기 여유 확인 ==========
        if qty > item['max_movable']:
            violations.append(
                f"❌ [{idx}] {item_name}: 누적 여유 초과 "
                f"(요청: {qty:,}, 최대: {item['max_movable']:,})"
            )
            continue
        
        # ========== [검증 3] PLT 단위 확인 ==========
        if qty % item['plt'] != 0:
            violations.append(
                f"❌ [{idx}] {item_name}: PLT 단위 아님 "
                f"(요청: {qty:,}, PLT: {item['plt']})"
            )
            continue
        
        # ========== [검증 4] 목적지 파싱 ==========
        try:
            # "YYYY-MM-DD_라인" 형식 파싱
            if '_' in to_loc:
                parts = to_loc.split('_')
                to_date = parts[0].strip()
                to_line = '_'.join(parts[1:]).strip()  # "조립1" 같은 경우 대비
            else:
                raise ValueError("'_' 구분자 없음")
            
        except Exception as e:
            violations.append(
                f"❌ [{idx}] {item_name}: 목적지 형식 오류 "
                f"(입력: '{to_loc}', 예상: 'YYYY-MM-DD_라인')"
            )
            continue
        
        # ========== [검증 5] 물리 제약 확인 ==========
        # A2XX는 조립3 금지
        if item['is_a2xx'] and to_line == "조립3":
            violations.append(
                f"❌ [{idx}] {item_name}: A2XX는 조립3 이동 불가 (물리 제약)"
            )
            continue
        
        # 전용 모델은 타라인 금지
        if not item['is_t6'] and not item['is_a2xx'] and to_line != target_line:
            violations.append(
                f"❌ [{idx}] {item_name}: 전용 모델은 타라인 이동 불가 "
                f"(현재: {target_line}, 요청: {to_line})"
            )
            continue
        
        # ========== [검증 6] CAPA 확인 및 조정 ==========
        capa_key = f"{to_date}_{to_line}"
        if capa_key not in capa_status:
            violations.append(
                f"⚠️ [{idx}] {item_name}: 목적지 CAPA 정보 없음 ({capa_key})"
            )
            continue
        
        dest_capa = capa_status[capa_key]
        
        if qty > dest_capa['remaining']:
            # CAPA 부족 시 자동 조정 시도
            if dest_capa['remaining'] >= item['plt']:
                # 남은 CAPA 내에서 최대 PLT 단위로 조정
                adj_plts = dest_capa['remaining'] // item['plt']
                adj_qty = adj_plts * item['plt']
                
                move['qty'] = adj_qty
                move['plt'] = adj_plts
                move['adjusted'] = True
                move['original_qty'] = qty
                
                capa_status[capa_key]['remaining'] -= adj_qty
                
                violations.append(
                    f"✅ [{idx}] {item_name}: CAPA 부족으로 자동 조정 "
                    f"({qty:,}개 → {adj_qty:,}개)"
                )
            else:
                violations.append(
                    f"❌ [{idx}] {item_name}: CAPA 부족 및 조정 불가 "
                    f"(요청: {qty:,}, 남은 CAPA: {dest_capa['remaining']:,})"
                )
                continue
        else:
            # CAPA 충분 - 차감
            capa_status[capa_key]['remaining'] -= qty
            move['adjusted'] = False
            move['plt'] = qty // item['plt']
        
        # ========== [검증 7] 가동일 확인 ==========
        if not is_workday_in_db(plan_df, to_date):
            violations.append(
                f"❌ [{idx}] {item_name}: {to_date}는 휴무일입니다."
            )
            continue
        
        # ========== 모든 검증 통과 ==========
        validated_moves.append(move)
    
    return validated_moves, violations


def is_workday_in_db(plan_df, date_str):
    """
    특정 날짜가 가동일인지 확인
    
    Args:
        plan_df (DataFrame): 생산 계획 데이터 (is_workday 컬럼 필요)
        date_str (str): 확인할 날짜 (YYYY-MM-DD)
    
    Returns:
        bool: 가동일 여부 (True: 가동일, False: 휴무일 또는 정보 없음)
    """
    if plan_df.empty or 'is_workday' not in plan_df.columns:
        return False
    
    date_info = plan_df[plan_df['plan_date'] == date_str]
    
    if not date_info.empty:
        return bool(date_info.iloc[0]['is_workday'])
    
    return False
