# 第一階段：同步 Watch Backend

本階段提供可獨立使用的 `AdbTransportListWatchBackend` 與 AOSP adapter
`SmartSocketAdbTransportListWatchBackend`。既有 controller、supervision、runtime
仍使用原本的 Attachment 接線；新 Backend 尚未接入 runtime。

## 契約

- 呼叫者序列執行 `open()`、讀取與 `close()`，不重疊使用。沒有建立途中取消、
  跨執行緒中斷讀取、自動重連或內部 worker。
- 每個 Backend 最多持有一個未關閉的 Session。重複 `open()` 拋出 `RuntimeError`，
  原 Session 與其資源保持不變。
- `open(endpoint, startup_timeout_seconds=5.0)` 完成連線、握手、第一份完整清單的
  讀取與解析後，才配發 Identity 並回傳 Session。空清單也是成功結果。
- Session 沿用 `session_identity`、`initial`、`updates()`、`close()` 介面。
  `initial` 不會在更新中重播；每次 `updates()` 回傳同一條單一消費者 iterator。
- 同一 Session 的 Identity 固定。重新建立使用新 Identity；序號不保證連續。
- Session 是 socket 的唯一資源 owner。`close()` 終止持有並釋放資源，可重複呼叫；
  關閉後的迭代結束，不再讀 socket。舊 Session 不會關閉新 Session 的資源。
- 預期 I/O、service、protocol 失敗使用既有 `AdbTransportListWatchError` 分類。
  建立失敗清理本次資源；讀取失敗先關閉 Session 再拋出錯誤。EOF 是連線失敗。
- 明確關閉的 socket 清理錯誤會回報為 connection failure，邏輯持有仍然終止。
  已有主要失敗時，次要清理錯誤不覆蓋主因；程式錯誤原樣傳遞。
- 提前停止迭代仍須明確關閉 Session。沒有資料時，阻塞讀取會持續等待；本契約不提供
  從其他執行緒立即停止等待的能力。

啟動 deadline 從同步 DNS 解析完成後開始，涵蓋所有連線候選、握手與第一份清單。
建立成功後切換為無讀取 timeout 的阻塞模式。

## 與 Server 的對齊

沿用 Server Backend 的資源封裝、外部注入 issuer、取得可用資源後配置 Identity，
以及建立失敗時回收資源的安排。新 Backend 的 Factory 形狀為
`factory(identity_issuer) -> backend`；AOSP Backend 建構子可直接用作 Factory。

Backend 配發 Identity，Domain 決定其 authority。新 Session 不持有 coordinator，
不操作 StateStore，也不發布 lifecycle 事件。後续接線由 Coordinator 先撤銷 authority，
再呼叫 Session 的 `close()`。既有 Session binder 的 authority 行為暫時保留。

## 獨立使用

```python
from adb.adapters.aosp.watch_backend import SmartSocketAdbTransportListWatchBackend
from adb.transport_list.session_identity import AdbTransportListSessionIdentityIssuer
from networking import TcpAddress

issuer = AdbTransportListSessionIdentityIssuer()  # 同一 runtime 作用域共用
backend = SmartSocketAdbTransportListWatchBackend(issuer)
session = backend.open(TcpAddress("127.0.0.1", 5037))
try:
    print(session.session_identity, session.initial)
    for transport_list in session.updates():
        print(session.session_identity, transport_list)
finally:
    session.close()
```

## 遷移邊界與驗證

本階段保留舊 Attachment、Stream、binder、controller 與所有既有匯出。
上層改用新 Backend、StateStore 接受外部配發的 Session Identity，以及舊抽象移除，
留待後續階段。最終公開資源模型為 `Backend.open() -> WatchSession`。

執行 `python -B -m unittest discover -s tests -p 'test_*.py' -v` 驗證契約。
測試使用 fake socket 與 clock，不需要 ADB server 或裝置；另保留舊 Watch 與 controller
的建立／關閉相容性測試。
