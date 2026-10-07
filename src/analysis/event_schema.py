# -*- coding: utf-8 -*-
"""
이벤트 이름 -> 공정(process) · 상태(state) 규칙 [명세서 8.1 · 10절].

  process_of(event) : 명세서 8.1절 공정명. 매핑이 없으면 KeyError (새 이벤트를 추가하면 여기도 추가할 것)
  state_of(entity_type, event) : 이벤트 직후 대상의 상태
      *_QUEUE_ENTER            -> Waiting     (설비·자원 대기)
      TRANSPORT_*_START        -> Moving
      *_START                  -> Processing
      *_END                    -> Waiting     (다음 공정 대기)
      PRINT_FAILED / INSPECTION_FAILED -> Failed,  SCRAPPED -> Scrapped
      PART_COMPLETED / ORDER_COMPLETED -> Done
      그 외 (접수·배치 형성)     -> Waiting
      MACHINE (장비 상태 6종, 명세서 11절): 이벤트 = 상태가 바뀐 순간
          IDLE_START / WAITING_START / SETUP_START / RUNNING_START -> 각 상태
          DOWN_START (고장 수리) -> Down,  MAINTENANCE_START (PM) / CLEANING_START (세척액 교체) -> Maintenance
          DOWN_END / MAINTENANCE_END / CLEANING_END -> Idle
"""

MACHINE_STATES = ("Idle", "Setup", "Running", "Waiting", "Down", "Maintenance")
_MACHINE_EVENT_STATE = {"IDLE_START": "Idle", "WAITING_START": "Waiting", "SETUP_START": "Setup",
                        "RUNNING_START": "Running", "DOWN_START": "Down", "MAINTENANCE_START": "Maintenance",
                        "CLEANING_START": "Maintenance"}

PROCESSES = ("Order Reception", "Job Assignment", "Batch Formation", "VPP Build", "Part Removal", "Washing",
             "UV Curing", "Support Removal", "Surface Treatment", "Inspection", "Packaging", "Transport")

# 이벤트 기본 이름(접미사 _START/_END/_QUEUE_ENTER 제거) -> 공정
_PROCESS = {
    "ORDER_RECEIVED": "Order Reception",
    "JOB_ASSIGNMENT": "Job Assignment",
    "JOB_ASSIGNMENT_REWORK": "Job Assignment",
    "BATCH_OPENED": "Batch Formation",
    "ADDED_TO_BATCH": "Batch Formation",
    "BATCH_CLOSED": "Batch Formation",
    "BUILD_PREPARATION": "VPP Build",          # 명세서 공정 목록에 별도 항목 없음 -> 출력 준비로 분류
    "PRINTER": "VPP Build",                    # PRINTER_QUEUE_ENTER
    "VPP_BUILD": "VPP Build",
    "PRINT_FAILED": "VPP Build",
    "PART_REMOVAL": "Part Removal",
    "WASHING": "Washing",
    "UV_CURING": "UV Curing",
    "SUPPORT_REMOVAL": "Support Removal",
    "SURFACE_TREATMENT": "Surface Treatment",
    "INSPECTION": "Inspection",
    "INSPECTION_FAILED": "Inspection",
    "PACKAGING": "Packaging",
    "PART_COMPLETED": "Packaging",
}

_SUFFIXES = ("_QUEUE_ENTER", "_START", "_END")


def base_name(event):
    for s in _SUFFIXES:
        if event.endswith(s):
            return event[: -len(s)]
    return event


def process_of(event):
    base = base_name(event)
    if base.startswith("TRANSPORT_"):
        return "Transport"
    return _PROCESS[base]


def state_of(entity_type, event):
    if event.endswith("_QUEUE_ENTER"):
        return "Waiting"
    if entity_type == "MACHINE":
        return _MACHINE_EVENT_STATE.get(event, "Idle")
    if event.endswith("_START"):
        return "Moving" if event.startswith("TRANSPORT_") else "Processing"
    if event.endswith("_FAILED"):
        return "Failed"
    if event == "SCRAPPED":
        return "Scrapped"
    if event.endswith("_COMPLETED"):
        return "Done"
    return "Waiting"


def order_ids(ref):
    """관련 주문 번호: 주문·부품은 1개, 배치·로드(부품 목록)는 등장 순서대로 ';' 로 연결."""
    if ref is None:
        return ""
    if hasattr(ref, "parts"):                                  # Batch
        ref = ref.parts
    if isinstance(ref, (list, tuple)):
        return ";".join(dict.fromkeys(p.order.order_id for p in ref))
    if hasattr(ref, "order"):                                  # Part
        return ref.order.order_id
    return ref.order_id                                        # Order
