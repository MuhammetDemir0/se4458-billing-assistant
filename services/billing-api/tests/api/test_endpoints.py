"""HTTP contract tests: every operation has success and failure cases.

The real FastAPI app runs in-process; only the database (in-memory fakes) and
the token issuer (test HS256 key) are replaced through dependency injection.
"""

from tests.conftest import OTHER, OWNER, auth

BASE = "/api/v1/subscribers"


def add_usage(client, headers, usage_type="PHONE", n=1, no=OWNER, month=10):
    for _ in range(n):
        r = client.post(f"{BASE}/{no}/usage", json={"month": month, "year": 2026, "usageType": usage_type},
                        headers=headers)
        assert r.status_code == 201, r.text


def calculate(client, headers, no=OWNER, month=10):
    return client.post(f"{BASE}/{no}/bills/2026/{month}/calculate", headers=headers)


def assert_problem(response, status, code):
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == status and body["code"] == code
    return body


# ---------- Add Usage ----------

def test_add_usage_success(client, admin_headers):
    r = client.post(f"{BASE}/{OWNER}/usage", json={"month": 10, "year": 2026, "usageType": "INTERNET"},
                    headers=admin_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["transactionStatus"] == "SUCCESS"
    assert body["record"]["quantity"] == 1 and body["record"]["unit"] == "MB"


def test_add_usage_requires_token(client):
    r = client.post(f"{BASE}/{OWNER}/usage", json={"month": 10, "year": 2026, "usageType": "PHONE"})
    assert_problem(r, 401, "UNAUTHORIZED")
    assert r.headers["www-authenticate"] == "Bearer"


def test_add_usage_requires_admin_role(client, owner_headers):
    r = client.post(f"{BASE}/{OWNER}/usage", json={"month": 10, "year": 2026, "usageType": "PHONE"},
                    headers=owner_headers)
    assert_problem(r, 403, "ADMIN_REQUIRED")


def test_add_usage_validates_body(client, admin_headers):
    r = client.post(f"{BASE}/{OWNER}/usage", json={"month": 13, "year": 2026, "usageType": "SMS"},
                    headers=admin_headers)
    body = assert_problem(r, 400, "VALIDATION_ERROR")
    fields = {e["field"] for e in body["errors"]}
    assert {"month", "usageType"} <= fields


def test_add_usage_unknown_subscriber(client, admin_headers):
    r = client.post(f"{BASE}/5559999999/usage", json={"month": 10, "year": 2026, "usageType": "PHONE"},
                    headers=admin_headers)
    assert_problem(r, 404, "SUBSCRIBER_NOT_FOUND")


def test_invalid_subscriber_number_format(client, admin_headers):
    r = client.post(f"{BASE}/12ab/usage", json={"month": 10, "year": 2026, "usageType": "PHONE"},
                    headers=admin_headers)
    assert_problem(r, 400, "VALIDATION_ERROR")


# ---------- Calculate Bill ----------

def test_calculate_bill_success(client, admin_headers):
    add_usage(client, admin_headers, "PHONE", 101)  # 1,010 minutes
    r = calculate(client, admin_headers)
    assert r.status_code == 200
    bill = r.json()["bill"]
    assert bill["billTotal"] == 60.0 and bill["remainingBalance"] == 60.0 and bill["status"] == "UNPAID"


def test_calculate_bill_rejects_subscriber_token(client, owner_headers):
    assert_problem(calculate(client, owner_headers), 403, "ADMIN_REQUIRED")


def test_calculate_bill_invalid_month(client, admin_headers):
    assert_problem(calculate(client, admin_headers, month=13), 400, "VALIDATION_ERROR")


# ---------- Query Bill (public) ----------

def test_query_bill_is_public(client, admin_headers):
    calculate(client, admin_headers)
    r = client.get(f"{BASE}/{OWNER}/bills/2026/10")
    assert r.status_code == 200
    body = r.json()
    assert body == body | {"subscriberNo": OWNER, "billTotal": 50.0, "paidAmount": 0.0, "currency": "USD"}
    assert "X-Request-ID" in r.headers


def test_query_bill_not_calculated(client):
    assert_problem(client.get(f"{BASE}/{OWNER}/bills/2026/3"), 404, "BILL_NOT_FOUND")


# ---------- Query Bill Detailed (owner or admin, paged) ----------

def test_query_bill_detailed_owner(client, admin_headers, owner_headers):
    add_usage(client, admin_headers, "PHONE", 3)
    add_usage(client, admin_headers, "INTERNET", 2)
    calculate(client, admin_headers)
    r = client.get(f"{BASE}/{OWNER}/bills/2026/10/details?page=1&pageSize=4", headers=owner_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["charges"]["phone"]["minutesUsed"] == 30
    assert body["charges"]["internet"]["mbUsed"] == 2
    assert body["charges"]["total"] == 50.0
    page = body["usageRecords"]
    assert (page["totalItems"], page["totalPages"], len(page["items"])) == (5, 2, 4)


def test_query_bill_detailed_admin_can_read_any(client, admin_headers):
    calculate(client, admin_headers)
    r = client.get(f"{BASE}/{OWNER}/bills/2026/10/details", headers=admin_headers)
    assert r.status_code == 200


def test_query_bill_detailed_requires_token(client):
    assert_problem(client.get(f"{BASE}/{OWNER}/bills/2026/10/details"), 401, "UNAUTHORIZED")


def test_query_bill_detailed_other_subscriber_forbidden(client, admin_headers, other_headers):
    """Knowing a subscriber number is not enough to read that subscriber's details."""
    calculate(client, admin_headers)
    r = client.get(f"{BASE}/{OWNER}/bills/2026/10/details", headers=other_headers)
    assert_problem(r, 403, "SUBSCRIBER_FORBIDDEN")


def test_query_bill_detailed_page_size_limit(client, owner_headers):
    r = client.get(f"{BASE}/{OWNER}/bills/2026/10/details?pageSize=1000", headers=owner_headers)
    assert_problem(r, 400, "VALIDATION_ERROR")


def test_expired_token_rejected(client):
    r = client.get(f"{BASE}/{OWNER}/bills/2026/10/details", headers=auth(OWNER, expires_in=-10))
    assert_problem(r, 401, "TOKEN_EXPIRED")


def test_token_with_wrong_signature_rejected(client):
    headers = auth(OWNER, secret="another-secret-that-is-long-enough-123")
    assert_problem(client.get(f"{BASE}/{OWNER}/bills/2026/10/details", headers=headers), 401, "INVALID_TOKEN")


def test_id_token_is_not_accepted(client):
    headers = auth(OWNER, token_use="id")
    assert_problem(client.get(f"{BASE}/{OWNER}/bills/2026/10/details", headers=headers), 401, "INVALID_TOKEN")


# ---------- Pay Bill ----------

def pay(client, headers, amount, tx, no=OWNER):
    return client.post(f"{BASE}/{no}/bills/2026/10/payments", json={"amount": amount, "transactionId": tx},
                       headers=headers)


def test_pay_bill_partial_then_full(client, admin_headers, owner_headers):
    calculate(client, admin_headers)  # $50
    r = pay(client, owner_headers, 20, "tx-api-000001")
    assert r.status_code == 201
    assert r.json() == r.json() | {"paymentStatus": "SUCCESS", "paidAmount": 20.0, "remainingBalance": 30.0,
                                   "billStatus": "PARTIALLY_PAID"}
    r = pay(client, owner_headers, 30, "tx-api-000002")
    assert r.json()["billStatus"] == "PAID"
    assert client.get(f"{BASE}/{OWNER}/bills/2026/10").json()["remainingBalance"] == 0.0


def test_pay_bill_duplicate_transaction(client, admin_headers, owner_headers):
    calculate(client, admin_headers)
    pay(client, owner_headers, 10, "tx-api-dup-01")
    assert_problem(pay(client, owner_headers, 10, "tx-api-dup-01"), 409, "DUPLICATE_TRANSACTION")
    assert client.get(f"{BASE}/{OWNER}/bills/2026/10").json()["paidAmount"] == 10.0


def test_pay_bill_amount_exceeds_balance(client, admin_headers, owner_headers):
    calculate(client, admin_headers)
    assert_problem(pay(client, owner_headers, 50.01, "tx-api-over-1"), 422, "AMOUNT_EXCEEDS_BALANCE")


def test_pay_bill_by_another_subscriber_forbidden(client, admin_headers, other_headers):
    calculate(client, admin_headers)
    assert_problem(pay(client, other_headers, 10, "tx-api-other1"), 403, "SUBSCRIBER_FORBIDDEN")


def test_pay_bill_requires_token(client):
    assert_problem(pay(client, {}, 10, "tx-api-noauth"), 401, "UNAUTHORIZED")


def test_pay_bill_validation(client, owner_headers):
    r = pay(client, owner_headers, -5, "short")
    body = assert_problem(r, 400, "VALIDATION_ERROR")
    assert {"amount", "transactionId"} <= {e["field"] for e in body["errors"]}


def test_pay_bill_not_calculated(client, owner_headers):
    assert_problem(pay(client, owner_headers, 10, "tx-api-nobill"), 404, "BILL_NOT_FOUND")


# ---------- Documentation ----------

def test_openapi_and_swagger_are_versioned(client):
    spec = client.get("/api/v1/openapi.json").json()
    assert spec["info"]["version"] == "1.0.0"
    assert all(path.startswith("/api/v1/") for path in spec["paths"])
    assert client.get("/api/v1/docs").status_code == 200


def test_other_subscriber_fixture_is_distinct():
    assert OWNER != OTHER
