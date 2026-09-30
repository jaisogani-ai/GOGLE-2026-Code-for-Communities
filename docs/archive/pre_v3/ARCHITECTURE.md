# TATHYON: SYSTEM ARCHITECTURE & MATHEMATICAL SPECIFICATION
**Healthcare Resource Resilience Control Plane**
*Document Version: 1.0.0 | Date: September 2026*
*Author: Principal Systems Architect*

---

## 1. COMPONENT ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          FASTAPI APPLICATION GATEWAY                         │
│                    (REST API / Authentication / RBAC)                       │
└──────┬─────────────────┬───────────────────┬───────────────────┬────────────┘
       │                 │                   │                   │
       ▼                 ▼                   ▼                   ▼
┌──────────────┐  ┌──────────────┐   ┌───────────────┐   ┌────────────────────┐
│   GRAPH &    │  │  FORECAST &  │   │ Resilience Scenario Engine  │   │  RESPONSE PLANNER  │
│ FUSION LAYER │  │ SURGE ENGINE │   │  SIMULATION   │   │  (OR-TOOLS SOLVER) │
│ (graph.py)   │  │(stockout.py) │   │  (twin.py)    │   │   (planner.py)     │
└──────┬───────┘  └──────┬───────┘   └───────┬───────┘   └─────────┬──────────┘
       │                 │                   │                     │
       └─────────────────┴───────────────────┴─────────────────────┘
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │   TRUST & VERIFICATION KERNEL │
                      │  (EventStore / SHA-256 Chain) │
                      │       (event_store.py)        │
                      └───────────────────────────────┘
```

---

## 2. MATHEMATICAL FORMULATIONS

### 2.1 Intermittent Demand Forecasting & Competition
For public health clinics with intermittent consumption (frequent zero days):
1. **Croston Method:** Decomposes demand into non-zero demand size $z_t$ and inter-arrival time $p_t$:
   $$z_t = \alpha y_t + (1 - \alpha) z_{t-1}$$
   $$p_t = \alpha q_t + (1 - \alpha) p_{t-1}$$
   $$\hat{y}_{t+h} = \frac{z_t}{p_t}$$
2. **Syntetos-Boylan Approximation (SBA):** Eliminates positive bias in classic Croston:
   $$\hat{y}_{t+h} = \left(1 - \frac{\alpha}{2}\right) \frac{z_t}{p_t}$$
3. **Teunter-Syntetos-Babai (TSB):** Updates demand probability $p_t$ every period rather than only on arrival periods:
   $$p_t = p_{t-1} + \beta (I_t - p_{t-1})$$
   $$\hat{y}_{t+h} = p_t \cdot z_t$$
4. **Holdout Evaluation Metric:** Mean Absolute Scaled Error (MASE):
   $$\text{MASE} = \frac{\sum_{t=1}^{H} |y_t - \hat{y}_t|}{\frac{H}{N-1}\sum_{i=2}^{N}|y_i - y_{i-1}|}$$
   The engine selects the model achieving minimum holdout MASE.

### 2.2 Stockout Probability Calculation
Let $S_t$ be the current usable inventory at facility $i$.
Let $L$ be the expected replenishment lead time in days.
Total demand over lead time $D_L = \sum_{k=1}^{L} d_{t+k}$ is modeled as a normal/gamma distribution with mean $\mu_L = L \cdot \hat{d}$ and standard deviation $\sigma_L = \sqrt{L} \cdot \sigma_d$.
The stockout probability within the replenishment window is:
$$P(\text{stockout}) = P(D_L > S_t) = 1 - \Phi\left(\frac{S_t - \mu_L}{\sigma_L}\right)$$

### 2.3 Surge Detection (Residual CUSUM)
Given forecast residual $e_t = y_t - \hat{y}_t$ and standard deviation $\sigma_e$:
Standardized residual: $z_t = \frac{e_t - k\sigma_e}{\sigma_e}$ (with allowance $k = 0.5$).
Cumulative sum:
$$C_t^+ = \max(0, C_{t-1}^+ + z_t)$$
Alert threshold tiers:
- $C_t^+ < 2.0 \implies \text{NORMAL}$
- $2.0 \le C_t^+ < 3.5 \implies \text{WATCH}$
- $3.5 \le C_t^+ < 5.0 \implies \text{ELEVATED}$
- $5.0 \le C_t^+ < 8.0 \implies \text{HIGH}$
- $C_t^+ \ge 8.0 \implies \text{CRITICAL}$

### 2.4 Response Planner Optimization (Mixed Integer Linear Program via CP-SAT)
Let $I$ be the set of donor facilities and $J$ be the set of shortage facilities.
Decision variable $x_{ij} \in \mathbb{Z}^+$ is the quantity of resource transferred from $i$ to $j$.
Binary variable $y_{ij} \in \{0, 1\}$ indicates whether a transfer route $(i, j)$ is activated.
Shortage penalty variable $s_j \ge 0$ represents unmet demand at destination $j$.

**Objective Function:**
$$\min \sum_{j \in J} W_{\text{shortage}} \cdot s_j + \sum_{i \in I}\sum_{j \in J} W_{\text{distance}} \cdot d_{ij} \cdot x_{ij} + \sum_{i \in I}\sum_{j \in J} W_{\text{transfer}} \cdot y_{ij} + \sum_{i \in I}\sum_{j \in J} W_{\text{expiry}} \cdot E_i \cdot x_{ij}$$
Where $W_{\text{shortage}} = 100,000$, $W_{\text{distance}} = 10$, $W_{\text{transfer}} = 500$, $W_{\text{expiry}} = 50$.

**Subject to Constraints:**
1. **Donor Surplus Limit:** $\sum_{j \in J} x_{ij} \le \max(0, S_i^{\text{usable}} - \text{SafetyFloor}_i), \quad \forall i \in I$
2. **Shortage Coverage:** $\sum_{i \in I} x_{ij} + s_j \ge \text{Deficit}_j, \quad \forall j \in J$
3. **Route Activation:** $x_{ij} \le M \cdot y_{ij}, \quad \forall i, j$
4. **Transport Vehicle Payload:** $x_{ij} \le \text{MaxPayload}_{ij}, \quad \forall i, j$
5. **Cold-Chain Transit Time:** $d_{ij} / v_{\text{speed}} \le \text{MaxColdChainHours}, \quad \forall (i, j)$
