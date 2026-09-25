# L3B Architecture Record — Multi-Agent MCP + A2A

Tài liệu thiết kế kiến trúc hệ thống Multi-Agent điều tra khiếu nại E-commerce (A2A Protocol & MCP Integration).

---

## 1. System Overview

Hệ thống hoạt động theo luồng phối hợp Đa Tác Tử (Agent-to-Agent - A2A) được điều phối bởi **Coordinator Agent**, kết hợp cùng các Specialist Agents và Verifier Agent để đưa ra kết luận nghiệp vụ chính xác, hợp lệ theo JSON Schema và tối ưu hiệu năng gọi MCP tool.

```text
                          ┌──────────────────────────┐
                          │   Coordinator / Router   │
                          └─────────────┬────────────┘
                                        │ (Handoff)
         ┌──────────────────────────────┼──────────────────────────────┐
         ▼                              ▼                              ▼
┌──────────────────┐           ┌──────────────────┐           ┌──────────────────┐
│ Entity / Customer│           │ Order / Item     │           │  Shipment        │
│ Resolver Agent   │           │ Specialist Agent │           │ Specialist Agent │
└────────┬─────────┘           └────────┬─────────┘           └────────┬─────────┘
         │                              │                              │
         └──────────────────────────────┼──────────────────────────────┘
                                        │ (MCP Evidence Collector)
                                        ▼
                               ┌──────────────────┐
                               │  Payment/Refund  │
                               │ Specialist Agent │
                               └────────┬─────────┘
                                        │ (Handoff)
                                        ▼
                               ┌──────────────────┐
                               │   Policy Agent   │
                               └────────┬─────────┘
                                        │ (Handoff)
                                        ▼
                               ┌──────────────────┐
                               │  Verifier Agent  │
                               └────────┬─────────┘
                                        │ (Validated Output)
                                        ▼
                                   [END OUTPUT]
```

---

## 2. Agent Ownership & Tool Permissions

Áp dụng nguyên tắc **Least Privilege** — Mỗi Agent chỉ được cấp quyền sử dụng các MCP Tool phục vụ đúng chuyên môn nghiệp vụ của mình.

| Actor | Input Data | Trách nhiệm chính | Tool Permissions | Output / Handoff Target |
| --- | --- | --- | --- | --- |
| **Coordinator** | Case payload | Khởi tạo, điều phối luồng handoff giữa các tác tử, phát sinh trace lifecycle | Không trực tiếp gọi MCP | Handoff sang `entity-resolver`, `order-agent`, `shipment-agent`, `payment-agent`, `policy-agent`, `verifier-agent` |
| **Entity Resolver** | `exact_order_id`, `candidate_order_ids`, `customer_unique_id` | Xếp hạng và lọc các candidate orders, ánh xạ lịch sử khách hàng, đưa ra resolved order ID | `get_customer_history`, `get_order` | `resolved_order_ids`, `rejected_candidates`, handoff lại `coordinator` |
| **Order / Item Agent** | `resolved_order_ids` | Thu thập thông tin chi tiết đơn hàng, sản phẩm, thông tin người bán (seller) | `get_order`, `get_order_items`, `get_product_context`, `get_sellers` | `item_ids`, `seller_ids`, `order_statuses`, handoff lại `coordinator` |
| **Shipment Agent** | `resolved_order_ids` | Phân tích lộ trình vận chuyển, so sánh thời gian giao thực tế vs ước tính & hạn giao hàng người bán | `get_shipment_summary` | `shipment_analysis` (verdict, `late_seller_ids`, `timeline_complete`), handoff lại `coordinator` |
| **Payment Agent** | `resolved_order_ids` | Kiểm tra giao dịch thanh toán, lệch giá, trùng lặp thẻ, trạng thái hoàn tiền | `get_order_payments`, `get_payment_timeline`, `get_refund_timeline` | `payment_analysis` (verdict, totals BRL), handoff lại `coordinator` |
| **Policy Agent** | Kết quả từ các Specialist Agents | Ánh xạ chính sách nền tảng, xác định `primary_issue`, nguyên nhân gốc rễ (root cause) & mức bồi thường | `get_policy` | `primary_issue`, `root_cause_analysis`, `financial_resolution`, `resolution_actions` |
| **Verifier Agent** | Toàn bộ dữ liệu tổng hợp | Xác minh JSON Schema (`day09-l3b-output-v2`), kiểm tra tính nhất quán và bằng chứng | Không gọi MCP | Validated Output JSON, phát sinh `verification_completed` & `case_finalized` |

---

## 3. Entity Resolution và A2A Protocol

1. **Candidate Resolution & Thresholds**:
   - Nếu case có `exact_order_id`, chọn làm `resolved_order_ids` và loại bỏ các kandidat khác vào `rejected_candidates` với confidence = 0.95.
   - Nếu case chỉ có `candidate_order_ids` hoặc `customer_unique_id`, Agent gọi `get_customer_history` và `get_order` để đối soát. Nếu tìm thấy khớp nối chính xác (về `customer_unique_id` & timeline), candidate đó sẽ được chọn.
   - Trạng thái `status`: `"resolved"` (confidence 0.95), `"ambiguous"` (confidence 0.50), hoặc `"not_found"` (confidence 0.10).

2. **A2A Correlation & Handoff Protocol**:
   - Mọi thông điệp và sự kiện giữa các Agent đều được liên kết theo `case_id`.
   - Mỗi lần chuyển giao quyền xử lý giữa các Agent đều phải ghi trace log chuẩn `handoff` (Actor -> Target).
   - Tuyệt đối không lưu prompt private hoặc chain-of-thought suy luận vào trace event.

---

## 4. Evidence và Conflict Lifecycle

1. **MCP Response Validation**:
   - Mọi kết quả trả về từ MCP Gateway đều được kiểm tra qua `mcp-evidence-response-v1.schema.json`.
   - `evidence_ref` duy nhất (dạng `ev_...`) được lưu trữ vào bộ sưu tập `collected_evidence_refs`.
   - Mỗi khi một Agent tiêu thụ dữ liệu từ MCP tool, một trace event `tool_result_consumed` được tự động ghi lại.

2. **Isolation & Provenance Rules**:
   - Bằng chứng (`evidence_ref`) thuộc case nào chỉ được dùng cho case đó. Không dùng chéo giữa các case.
   - Field `evidence_refs` trong output cuối cùng chỉ chứa các `evidence_ref` thực sự đã được gọi và trả về từ MCP Gateway.

---

## 5. Failure Policy & Efficiency Strategy

| Failure Scenario | Budget / Limit | Alternative Fallback | Trace Event / Decision Code |
| --- | ---: | --- | --- |
| **MCP Tool Timeout / Exception** | Retry tối đa 2 lần | Bỏ qua tool lỗi, đánh giá dựa trên dữ liệu hiện có hoặc đặt `insufficient_evidence` | Log warning nội bộ, không làm sập workflow |
| **Entity Not Found / Ambiguous** | 1 query / candidate | Trả về `status: "not_found"` hoặc `"ambiguous"`, giảm confidence | `handoff`, `policy_decided` với `primary_issue: "insufficient_evidence"` |
| **Dữ liệu mâu thuẫn (Data Conflict)** | 1 Pass Audit | Ưu tiên dữ liệu từ MCP Gateway chính thống thay vì claim người dùng | Ghi nhận vào `data_conflicts` nếu có mâu thuẫn nguồn |
| **Specialist Result Không hợp lệ** | Không Retry | Sử dụng giá trị an toàn mặc định (safe default) | `verification_completed` với kiểm tra nghiêm ngặt |

### Efficiency Caching:
* **Per-Case Cache**: Tất cả các cuộc gọi MCP tool trong phạm vi 1 case đều được lưu cache theo `(tool_name, arguments)`. 
* Tránh tuyệt đối việc gọi lặp lại cùng một tool với cùng đối số giữa các Specialist Agents, giảm thiểu số lượng MCP call dư thừa để tối ưu điểm **efficiency**.

---

## 6. Verification Invariants

Trước khi xuất `outputs/<case_id>.json`, Verifier Agent thực hiện kiểm tra bắt buộc:

1. **Strict JSON Schema Compliance**: Output phải pass 100% kiểm tra `contracts.validate_output()`. Không chứa bất kỳ field dư thừa nào (`additionalProperties: false`).
2. **Evidence Ownership**: 100% `evidence_refs` nằm trong danh sách bằng chứng hợp lệ được trả về từ MCP Gateway.
3. **Confidence Bounds**: Giá trị `confidence` phải nằm trong khoảng $[0.0, 1.0]$.
4. **Currency Invariant**: Field `currency` trong `financial_resolution` luôn luôn là `"BRL"`.
5. **Trace Integrity**: Đảm bảo chuỗi sự kiện trace có đủ `case_received`, `task_assigned`, `tool_result_consumed`, `handoff`, `policy_decided`, `verification_completed` và `case_finalized`.

---

## 7. Reproducibility & Execution Settings

* **Python Version**: Python 3.11+
* **Dependencies**: `jsonschema`, `referencing`, `httpx2`, `mcp`, `pytest`
* **Concurrency Policy**: Xử lý async từng case hoặc batch theo giới hạn pool httpx2.
* **Secrets Policy**: Không lưu `COMPETITION_TEAM_API_KEY` hoặc bất kỳ secret nào trong `ARCHITECTURE.md`, trace log hay output artifacts.
