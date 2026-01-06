# main_engine.py
import json
import re
import google.generativeai as genai
from datetime import datetime, timedelta

# Part1: 데이터 수사 (품목 나열, 누적 납기, CAPA 분석)
import functions_part1  # 모듈 자체를 import
from functions_part1 import (
    step1_list_current_stock,
    step2_calculate_cumulative_slack,
    step3_analyze_destination_capacity
)

# Part2a: 제약 수사 (물리적 제약 정리, AI 팩트 보고서)
from functions_part2a import (
    step4_prepare_constraint_info,
    build_ai_fact_report
)

# Part2b: 최종 검증 (AI 전략 검증)
from functions_part2b import step6_validate_ai_strategy

# 보고서 생성
from reports import generate_full_report


def ask_professional_scheduler(question, plan_df, hist_df, product_map, plt_map, question_date, mode):
    """
    하이브리드 수사 엔진: Python 데이터 분석 + AI 전략 수립 + Python 검증
    
    Args:
        question (str): 사용자 질문
        plan_df (DataFrame): 생산 계획 데이터
        hist_df (DataFrame): 실적 데이터
        product_map (dict): 제품 정보 매핑
        plt_map (dict): PLT 정보 매핑
        question_date (str): 대상 날짜 (YYYY-MM-DD)
        mode (str): 실행 모드
    
    Returns:
        tuple: (report, success, charts, status_message)
    """
    
    # ========== [0단계] 초기화 ==========
    # functions_part1 모듈의 전역 변수 접근
    TODAY = functions_part1.TODAY
    CAPA_LIMITS = functions_part1.CAPA_LIMITS
    
    # 안전장치: TODAY가 None인 경우
    if TODAY is None:
        TODAY = datetime(2026, 1, 5).date()
    
    if CAPA_LIMITS is None:
        CAPA_LIMITS = {"조립1": 3300, "조립2": 3700, "조립3": 3600}
    
    today_str = TODAY.strftime('%Y-%m-%d')
    
    # 대상 라인 자동 감지 (개선 버전)
    target_line = None

    if "조립1" in question:
        target_line = "조립1"
    elif "조립2" in question:
        target_line = "조립2"
    elif "조립3" in question:
        target_line = "조립3"
    else:
        # 라인이 명시되지 않은 경우, 품목명 또는 데이터로 추론
        if not plan_df.empty:
            # 해당 날짜의 품목 데이터 확인
            date_data = plan_df[plan_df['plan_date'] == question_date]
            
            if not date_data.empty:
                # T6가 언급되었는지 확인
                if "T6" in question.upper():
                    t6_lines = date_data[date_data['product_name'].str.contains('T6', case=False, na=False)]['line'].unique()
                    if len(t6_lines) > 0:
                        target_line = t6_lines[0]
                
                # A2XX가 언급되었는지 확인
                elif "A2XX" in question.upper():
                    a2xx_lines = date_data[date_data['product_name'].str.contains('A2XX', case=False, na=False)]['line'].unique()
                    if len(a2xx_lines) > 0:
                        target_line = a2xx_lines[0]
                
                # J9가 언급되었는지 확인
                elif "J9" in question.upper():
                    j9_lines = date_data[date_data['product_name'].str.contains('J9', case=False, na=False)]['line'].unique()
                    if len(j9_lines) > 0:
                        target_line = j9_lines[0]
                
                # BERGSTROM이 언급되었는지 확인
                elif "BERGSTROM" in question.upper():
                    berg_lines = date_data[date_data['product_name'].str.contains('BERGSTROM', case=False, na=False)]['line'].unique()
                    if len(berg_lines) > 0:
                        target_line = berg_lines[0]
                
                # 그 외의 경우 해당 날짜에 생산량이 가장 많은 라인 선택
                else:
                    line_qty = date_data.groupby('line')['qty_1차'].sum()
                    if not line_qty.empty:
                        target_line = line_qty.idxmax()
    
    if not target_line:
        return "[ERROR] 질문에서 대상 라인을 찾을 수 없습니다. '조립1', '조립2', '조립3' 중 하나를 명시하거나, 품목명(T6, A2XX, J9 등)을 포함해주세요.", False, [], "[ERROR] 라인 미지정"
    
    # ========== [1단계] 품목/수량 나열 ==========
    stock_res, err = step1_list_current_stock(plan_df, question_date, target_line)
    if err:
        return f"[1단계 실패] {err}", False, [], "[ERROR] 품목 조회 실패"
    
    # ========== [2단계] 누적 납기 여유 계산 ==========
    items_with_slack = step2_calculate_cumulative_slack(plan_df, stock_res)
    
    if not items_with_slack:
        return "[2단계 실패] 이동 가능한 품목이 없습니다.", False, [], "[ERROR] 품목 분석 실패"

    # ========== [3단계] 목적지 CAPA 분석 ==========
    capa_status = step3_analyze_destination_capacity(plan_df, question_date, target_line)

    # ========== [4단계] 물리 제약 정리 ==========
    constraint_info = step4_prepare_constraint_info(items_with_slack, target_line)
    
    # ========== [5단계] AI 전략 수립 준비 ==========
    # 질문에서 CAPA 목표 비율 자동 추출
    capa_match = re.search(r'(\d+)%', question)
    
    # 샘플/추가 수량 직접 명시 확인
    sample_match = re.search(r'샘플\s*(\d+)', question)
    add_match = re.search(r'추가\s*(\d+)', question) or re.search(r'(\d+)\s*추가', question)
    
    if sample_match or add_match:
        # 샘플/추가 수량이 명시된 경우
        if sample_match:
            add_qty = int(sample_match.group(1))
        else:
            add_qty = int(add_match.group(1))
        
        target_qty = stock_res['total'] + add_qty
        reduction_needed = stock_res['total'] - target_qty  # 음수 (증량)
        capa_target = target_qty / CAPA_LIMITS[target_line]
    elif capa_match:
        capa_target = int(capa_match.group(1)) / 100
        target_qty = int(CAPA_LIMITS[target_line] * capa_target)
        reduction_needed = stock_res['total'] - target_qty
    else:
        capa_target = 0.75  # 기본값 75%
        target_qty = int(CAPA_LIMITS[target_line] * capa_target)
        reduction_needed = stock_res['total'] - target_qty
    
    # 증량/감축 판단
    if reduction_needed > 0:
        operation_mode = "reduce"
        operation_qty = reduction_needed
    elif reduction_needed < 0:
        operation_mode = "increase"
        operation_qty = abs(reduction_needed)
    else:
        return "[완료] 이미 목표 생산량에 도달했습니다.", True, [], "[OK] 조치 불필요"
    
    fact_report = build_ai_fact_report(
        constraint_info=constraint_info,
        capa_status=capa_status,
        target_date=question_date,
        target_line=target_line,
        reduction_needed=operation_qty
    )

    # ========== [AI 호출] 전략 수립 (프롬프트 개선) ==========
    ai_strategy = {}
    strategy_source = ""
    ai_failed = False
    ai_error_msg = ""
    
    try:
        ai_engine = genai.GenerativeModel('gemini-2.0-flash-exp')
        
        if operation_mode == "reduce":
            operation_desc = "감축"
            strategy_hint = """
**우선순위 전략 (위에서 아래 순서로):**
1. **같은 날 타라인 이송** (remaining > 0인 곳만)
   - T6 → 조립2 또는 조립3 (여유 있는 곳)
   - A2XX → 조립2만 가능
   
2. **같은 라인 미래 날짜 연기** (타라인 CAPA 부족 시)
   - {target_line}의 미래 가동일로 연기
   - 납기 여유(buffer_days) 범위 내에서만

3. **같은 라인 과거 선행 생산** (미래도 부족 시)
   - {target_line}의 과거 가동일로 당기기
   - 고정 기간({today_str} + 3일) 이후만 가능
"""
        else:
            operation_desc = "증량"
            strategy_hint = """
**우선순위 전략 (위에서 아래 순서로):**
1. **같은 날 타라인에서 가져오기** (T6만 가능)
   - 조립2, 조립3 → {target_line}
   
2. **같은 라인 미래 날짜에서 당기기**
   - {target_line}의 미래 가동일에서 당김
   - 납기 위반하지 않는 범위에서만
"""
        
        ai_prompt = f"""{fact_report}

위 데이터를 바탕으로 이동 조치 계획을 아래 JSON 형식으로 작성하라:

{{
  "strategy": "전략 요약 (한 문장)",
  "explanation": "전략 설명 (2-3문장)",
  "moves": [
    {{
      "item": "품목명",
      "qty": 수량,
      "plt": PLT수,
      "from": "출발지날짜_출발지라인",
      "to": "목적지날짜_목적지라인",
      "reason": "이유"
    }}
  ]
}}

**중요 규칙:**
1. "from", "to" 형식은 반드시 "YYYY-MM-DD_라인명" (예: "2026-01-10_조립2")
2. movable이 false인 품목은 절대 이동 금지
3. 목적지의 remaining을 초과하지 말 것
4. A2XX는 조립3 절대 금지, 전용 모델은 타라인 금지
5. qty는 반드시 PLT의 정수배

{strategy_hint}

**현재 상황 특이사항:**
- 대상 라인: {target_line} (자동 감지)
- 작업 모드: {operation_desc}
- 목표 {operation_desc}량: {operation_qty:,}개
- 사용자 요청 CAPA 목표: {int(capa_target*100)}%
"""
        
        response = ai_engine.generate_content(ai_prompt)
        raw_text = response.text.strip()
        
        # JSON 추출 (코드 블록 제거 후 파싱)
        json_text = re.sub(r'```json\s*|\s*```', '', raw_text)
        
        # 첫 번째 { 부터 마지막 } 까지 추출
        start = json_text.find('{')
        end = json_text.rfind('}') + 1
        
        if start != -1 and end > start:
            ai_strategy = json.loads(json_text[start:end])
            strategy_source = "AI 하이브리드 전략 (Gemini 2.0 Flash)"
        else:
            raise ValueError("JSON 형식을 찾을 수 없습니다.")
            
    except Exception as e:
        ai_strategy = {
            "strategy": "AI 전략 수립 실패로 폴백 전략 적용", 
            "explanation": "Python 기본 로직으로 대체",
            "moves": []
        }
        strategy_source = "Python 로직 (AI 오류로 폴백)"
        ai_failed = True
        ai_error_msg = str(e)

    # ========== [6단계] Python 최종 검증 ==========
    final_moves, violations = step6_validate_ai_strategy(
        ai_strategy=ai_strategy,
        constraint_info=constraint_info,
        capa_status=capa_status,
        plan_df=plan_df,
        target_line=target_line
    )

    # ========== [6.5단계] 증량/감축 분기 처리 ==========
    if operation_mode == "increase":
        # ===== 증량 로직 (미래/타라인에서 가져오기) =====
        current_increase = sum(m['qty'] for m in final_moves) if final_moves else 0
        remaining_needed = max(0, operation_qty - current_increase)
        
        if current_increase >= operation_qty * 0.9:
            # 이미 목표 달성
            pass
        else:
            # 증량 폴백 전략
            increase_moves = []
            increase_violations = []
            
            if final_moves:
                increase_violations.append(f"🔼 Python 증량 추가 전략: 현재 {current_increase:,}개 증량, 추가 {remaining_needed:,}개 필요")
            else:
                increase_violations.append(f"🔼 Python 증량 전략 활성화: 미래/타라인에서 가져오기 시도")
            
            question_date_obj = datetime.strptime(question_date, '%Y-%m-%d')
            
            # [1] 미래 날짜에서 가져오기
            future_sources = []
            for i in range(1, 11):  # 최대 10일 후까지
                future_date = (question_date_obj + timedelta(days=i)).date()
                future_date_str = future_date.strftime('%Y-%m-%d')
                
                if not plan_df.empty:
                    future_data = plan_df[
                        (plan_df['plan_date'] == future_date_str) & 
                        (plan_df['line'] == target_line)
                    ]
                    
                    if not future_data.empty:
                        for _, row in future_data.iterrows():
                            if row['qty_1차'] > 0:
                                future_sources.append({
                                    'date': future_date_str,
                                    'line': target_line,
                                    'item': row['product_name'],
                                    'qty': int(row['qty_1차']),
                                    'plt': int(row['plt']),
                                    'days_diff': i,
                                    'direction': 'future'
                                })
            
            # [2] 타라인에서 가져오기 (같은 날)
            transfer_sources = []
            for line in ["조립2", "조립3"]:
                if line == target_line:
                    continue
                
                transfer_data = plan_df[
                    (plan_df['plan_date'] == question_date) & 
                    (plan_df['line'] == line)
                ]
                
                if not transfer_data.empty:
                    for _, row in transfer_data.iterrows():
                        if row['qty_1차'] > 0:
                            # T6만 타라인 이동 가능
                            if "T6" in row['product_name'].upper():
                                transfer_sources.append({
                                    'date': question_date,
                                    'line': line,
                                    'item': row['product_name'],
                                    'qty': int(row['qty_1차']),
                                    'plt': int(row['plt']),
                                    'days_diff': 0,
                                    'direction': 'transfer'
                                })
            
            # [3] 우선순위 정렬 (타라인 → 미래)
            all_sources = transfer_sources + sorted(future_sources, key=lambda x: x['days_diff'])
            
            if not all_sources:
                increase_violations.append("❌ 가져올 수 있는 품목이 없습니다.")
            else:
                increase_violations.append(f"📅 가져올 수 있는 품목: 타라인 {len(transfer_sources)}개 + 미래 {len(future_sources)}개")
                
                # [4] 품목별로 가져오기
                for source in all_sources:
                    if remaining_needed <= 0:
                        break
                    
                    # PLT 단위로만 이동
                    max_plts = min(
                        source['qty'] // source['plt'],
                        remaining_needed // source['plt']
                    )
                    
                    if max_plts > 0:
                        move_qty = max_plts * source['plt']
                        
                        if source['direction'] == 'transfer':
                            from_location = f"{source['date']}_{source['line']}"
                            reason_text = f"🔄 타라인({source['line']})에서 가져오기"
                            direction_emoji = "🔄"
                        else:
                            from_location = f"{source['date']}_{source['line']}"
                            reason_text = f"⏪ 미래({source['days_diff']}일 후)에서 당기기"
                            direction_emoji = "⏪"
                        
                        increase_moves.append({
                            'item': source['item'],
                            'qty': move_qty,
                            'plt': max_plts,
                            'from': from_location,
                            'to': f"{question_date}_{target_line}",
                            'reason': reason_text,
                            'adjusted': False
                        })
                        
                        remaining_needed -= move_qty
                        
                        increase_violations.append(
                            f"✅ {source['item']}: {move_qty:,}개 {direction_emoji} {from_location}"
                        )
                        
                        # 목표 달성 시 중단
                        total_increased = current_increase + sum(m['qty'] for m in increase_moves)
                        if total_increased >= operation_qty * 0.9:
                            increase_violations.append(
                                f"🎯 목표 90% 달성 ({total_increased:,}개 / {operation_qty:,}개)"
                            )
                            break
            
            if increase_moves:
                final_moves = final_moves + increase_moves
                violations = violations + increase_violations
                strategy_source = f"{strategy_source} + Python 증량 전략"
                ai_strategy['explanation'] = f"{ai_strategy.get('explanation', '')} [Python 증량 추가: {len(increase_moves)}건]"
            else:
                increase_violations.append("❌ 증량 전략 실패: 가져올 품목 없음")
                violations = violations + increase_violations
    
    elif operation_mode == "reduce":
        # ===== 감축 로직 (기존 폴백 전략) =====
        if constraint_info:
            current_reduction = sum(m['qty'] for m in final_moves) if final_moves else 0
            remaining_needed = max(0, operation_qty - current_reduction)
            
            if current_reduction >= operation_qty * 0.9:
                pass
            else:
                fallback_moves = []
                fallback_violations = []
                
                # ===== [0] 이미 이동한 품목의 수량 계산 (부분 이동 허용) =====
                moved_qty_by_item = {}
                if final_moves:
                    for move in final_moves:
                        item_name = move['item']
                        moved_qty_by_item[item_name] = moved_qty_by_item.get(item_name, 0) + move['qty']
                
                # 이동 가능 품목 필터링 (부분 이동 허용)
                movable_items = []
                for item in constraint_info:
                    already_moved = moved_qty_by_item.get(item['name'], 0)
                    remaining_movable = item['max_movable'] - already_moved
                    
                    if remaining_movable >= item['plt']:
                        item_copy = item.copy()
                        item_copy['max_movable'] = remaining_movable
                        movable_items.append(item_copy)
                
                if not movable_items:
                    fallback_violations.append("❌ 모든 품목이 이미 최대치로 이동되어 추가 이동 불가")
                    violations = violations + fallback_violations
                elif movable_items:
                    if final_moves:
                        fallback_violations.append(f"🔄 Python 폴백 추가 전략: 현재 {current_reduction:,}개 감축, 추가 {remaining_needed:,}개 필요")
                    else:
                        fallback_violations.append("🔄 Python 폴백 전략 활성화: 타라인 이송 + 과거 선행 + 미래 연기 시도")
                    
                    question_date_obj = datetime.strptime(question_date, '%Y-%m-%d')
                    
                    # ===== [1] 타라인 이송 가능 날짜 추가 (같은 날) =====
                    transfer_dates = []
                    for line in ["조립2", "조립3"]:
                        if line == target_line:
                            continue
                        
                        key = f"{question_date}_{line}"
                        if key in capa_status and capa_status[key]['remaining'] > 0:
                            transfer_dates.append({
                                'date': question_date,
                                'line': line,
                                'remaining': capa_status[key]['remaining'],
                                'current': capa_status[key]['current'],
                                'days_diff': 0,
                                'direction': 'transfer'
                            })
                    
                    # ===== [2] 미래 날짜 탐색 (최대 15일) =====
                    future_dates_to_check = []
                    
                    for i in range(1, 16):
                        future_date = (question_date_obj + timedelta(days=i)).date()
                        future_date_str = future_date.strftime('%Y-%m-%d')
                        
                        if not plan_df.empty:
                            date_info = plan_df[plan_df['plan_date'] == future_date_str]
                            if not date_info.empty:
                                is_work = date_info.iloc[0].get('is_workday', False)
                                if is_work:
                                    current_qty = plan_df[
                                        (plan_df['plan_date'] == future_date_str) & 
                                        (plan_df['line'] == target_line)
                                    ]['qty_1차'].sum()
                                    
                                    remaining = CAPA_LIMITS[target_line] - current_qty
                                    
                                    future_dates_to_check.append({
                                        'date': future_date_str,
                                        'remaining': int(remaining),
                                        'current': int(current_qty),
                                        'days_diff': i,
                                        'direction': 'future'
                                    })
                    
                    # ===== [3] 과거 날짜 탐색 (고정 기간 이후, 스마트 범위 계산) =====
                    past_dates_to_check = []
                    frozen_date_obj = TODAY + timedelta(days=3)
                    frozen_date_str = frozen_date_obj.strftime('%Y-%m-%d')
                    
                    days_from_today = (question_date_obj.date() - TODAY).days
                    
                    if days_from_today <= 7:
                        past_range = 3
                    elif days_from_today <= 14:
                        past_range = 2
                    else:
                        past_range = 1
                    
                    for i in range(1, past_range + 1):
                        past_date = (question_date_obj - timedelta(days=i)).date()
                        past_date_str = past_date.strftime('%Y-%m-%d')
                        
                        if past_date_str < frozen_date_str or past_date_str < today_str:
                            continue
                        
                        if not plan_df.empty:
                            date_info = plan_df[plan_df['plan_date'] == past_date_str]
                            if not date_info.empty:
                                is_work = date_info.iloc[0].get('is_workday', False)
                                if is_work:
                                    current_qty = plan_df[
                                        (plan_df['plan_date'] == past_date_str) & 
                                        (plan_df['line'] == target_line)
                                    ]['qty_1차'].sum()
                                    
                                    remaining = CAPA_LIMITS[target_line] - current_qty
                                    
                                    past_dates_to_check.append({
                                        'date': past_date_str,
                                        'remaining': int(remaining),
                                        'current': int(current_qty),
                                        'days_diff': -i,
                                        'direction': 'past'
                                    })
                    
                    # ===== [4] 우선순위 정렬 (타라인 이송 → 과거 → 미래) =====
                    all_dates = transfer_dates + \
                               sorted(past_dates_to_check, key=lambda x: x['days_diff']) + \
                               sorted(future_dates_to_check, key=lambda x: x['days_diff'])
                    
                    if not all_dates:
                        fallback_violations.append("❌ 이동 가능한 날짜 정보를 찾을 수 없습니다.")
                    else:
                        fallback_violations.append(
                            f"📅 이동 가능 날짜: 타라인 {len(transfer_dates)}개 + 과거 {len(past_dates_to_check)}일 + 미래 {len(future_dates_to_check)}일 = 총 {len(all_dates)}개"
                        )
                        
                        # ===== [5] 품목별로 최적 날짜 찾기 =====
                        for item in movable_items:
                            moved = False
                            
                            for date_info in all_dates:
                                if date_info['remaining'] < item['plt']:
                                    continue
                                
                                if date_info['direction'] == 'transfer':
                                    if item['is_a2xx'] and date_info['line'] == "조립3":
                                        continue
                                    if not item['is_t6'] and not item['is_a2xx']:
                                        continue
                                
                                if date_info['direction'] == 'future':
                                    if abs(date_info['days_diff']) > item['buffer_days']:
                                        continue
                                
                                max_plts = min(
                                    item['max_movable'] // item['plt'],
                                    date_info['remaining'] // item['plt']
                                )
                                
                                if max_plts > 0:
                                    move_qty = max_plts * item['plt']
                                    
                                    if date_info['direction'] == 'transfer':
                                        to_location = f"{date_info['date']}_{date_info['line']}"
                                        reason_text = f"🔄 타라인 이송 ({date_info['line']}, 잔여: {date_info['remaining']:,}개)"
                                        direction_emoji = "🔄"
                                    elif date_info['direction'] == 'past':
                                        to_location = f"{date_info['date']}_{target_line}"
                                        reason_text = f"⏪ 선행 생산 ({abs(date_info['days_diff'])}일 전으로 당김, 목적지 잔여: {date_info['remaining']:,}개)"
                                        direction_emoji = "⏪"
                                    else:
                                        to_location = f"{date_info['date']}_{target_line}"
                                        reason_text = f"⏩ 미래 연기 ({date_info['days_diff']}일 후, 납기 여유: {item['buffer_days']}일, 목적지 잔여: {date_info['remaining']:,}개)"
                                        direction_emoji = "⏩"
                                    
                                    fallback_moves.append({
                                        'item': item['name'],
                                        'qty': move_qty,
                                        'plt': max_plts,
                                        'from': f"{question_date}_{target_line}",
                                        'to': to_location,
                                        'reason': reason_text,
                                        'adjusted': False
                                    })
                                    
                                    date_info['remaining'] -= move_qty
                                    
                                    fallback_violations.append(
                                        f"✅ {item['name']}: {move_qty:,}개 {direction_emoji} {to_location}"
                                    )
                                    
                                    moved = True
                                    
                                    total_reduced = current_reduction + sum(m['qty'] for m in fallback_moves)
                                    if total_reduced >= operation_qty * 0.9:
                                        fallback_violations.append(
                                            f"🎯 목표 90% 달성 ({total_reduced:,}개 / {operation_qty:,}개)"
                                        )
                                        break
                            
                            if moved:
                                total_reduced = current_reduction + sum(m['qty'] for m in fallback_moves)
                                if total_reduced >= operation_qty * 0.9:
                                    break
                    
                    if fallback_moves:
                        final_moves = final_moves + fallback_moves
                        violations = violations + fallback_violations
                        if not strategy_source.startswith("Python"):
                            strategy_source = f"{strategy_source} + Python 폴백 보강"
                        ai_strategy['explanation'] = f"{ai_strategy.get('explanation', '')} [Python 폴백 추가: {len(fallback_moves)}건]"
                    else:
                        if all_dates:
                            fallback_violations.append(
                                f"❌ 폴백 전략 실패: 총 {len(all_dates)}개 날짜 중 CAPA 부족 또는 제약으로 이동 불가"
                            )
                        else:
                            fallback_violations.append("❌ 폴백 전략 실패: 이동 가능한 날짜 정보 없음")
                        violations = violations + fallback_violations

    # ========== [7단계] 보고서 생성 ==========
    report = generate_full_report(
        stock_result=stock_res,
        items_with_slack=items_with_slack,
        capa_status=capa_status,
        constraint_info=constraint_info,
        ai_strategy=ai_strategy,
        final_moves=final_moves,
        violations=violations,
        target_qty=target_qty,
        capa_target=capa_target,
        reduction_needed=abs(reduction_needed),
        strategy_source=strategy_source,
        ai_failed=ai_failed,
        ai_error=ai_error_msg,
        today_str=today_str,
        question_date=question_date,
        target_line=target_line
    )
    
    return report, True, [], "[OK] 하이브리드 수사 완료"
