# Vận hành nhật ký giao dịch ba trụ cột

> **Đây là nguồn tham chiếu chính thức cho cách Trade Compass áp dụng khung Tâm lý, Quản lý rủi ro và Hệ thống giao dịch.**
>
> Ứng dụng hiển thị tệp này tại trang **Hướng dẫn**. Nội dung luôn được cập nhật theo nhãn và quy trình hiện tại của ứng dụng.

Nhật ký đo lường *chất lượng của một giao dịch đã đóng*, không chỉ P&L. Nó chủ đích là công cụ sau giao dịch và tư vấn: không bao giờ phê duyệt, chặn, thay đổi hoặc gửi lệnh MT5.

## Khung này trả lời điều gì?

| Trụ cột | Câu hỏi | Phạm vi theo dõi |
|---|---|---|
| Tâm lý | Tôi đã thực hiện bản thân đúng cách chưa? | Toàn bộ tài khoản đang hoạt động của trader |
| Quản lý rủi ro | Tôi có bảo vệ vốn và tuân thủ chính sách tài khoản không? | Tài khoản MT5 được chọn |
| Hệ thống giao dịch | Tôi có thực hiện một setup hợp lệ, đã được ghi chép không? | Tài khoản MT5 được chọn (mỗi tài khoản là một hệ thống độc lập) |

Một giao dịch có lãi vẫn có thể là **Lệnh thắng xấu** khi quy trình thất bại. Một giao dịch lỗ nhưng tuân thủ có thể là **Lệnh thua tốt**. P&L và chất lượng quy trình được tách riêng một cách có chủ đích.

## Vòng lặp vận hành

```text
MT5 đóng một vị thế
        ↓
Nhập dữ liệu thực hiện thực tế
        ↓
Có thể gộp các vị thế chia lệnh thành một giao dịch logic
        ↓
Đánh giá giao dịch đã đóng theo cả ba trụ cột
        ↓
Theo dõi 20, 30 hoặc 50 đánh giá đầy đủ gần nhất
        ↓
Lưu phản hồi tuần hoặc tháng
        ↓
Chọn một hành động cải thiện và kiểm chứng nó
```

Nhật ký bắt đầu từ giao dịch đã đóng. Nó không tuyên bố có thể tái dựng mọi quyết định khi lệnh đang mở, rủi ro mở hay cảm xúc từ dữ liệu MT5.

## Giao dịch logic và vị thế chia lệnh

MT5 xuất một **vị thế** đã đóng cho mỗi dòng. Nhật ký tự ánh xạ mỗi vị thế đã nhập thành một **giao dịch logic** riêng. Một ý tưởng giao dịch có thể có nhiều lần vào hoặc thoát lệnh theo tầng, vì vậy **Định hướng → Đánh giá** có thể gộp các vị thế tương thích thành một giao dịch logic.

| Lớp dữ liệu | Điều luôn đúng |
|---|---|
| Vị thế đã nhập | Sự kiện thực hiện MT5 bất biến: ID vị thế, thời gian, giá, khối lượng và P&L không bao giờ bị thay đổi. |
| Giao dịch logic | Mặc định là một vị thế, hoặc nhóm do người dùng tạo gồm từ hai vị thế trở lên. Nó nhận một đánh giá và một điểm quy trình. |
| Theo dõi rủi ro tài khoản | Tiếp tục dùng các vị thế gốc theo thứ tự thời gian; một nhóm không thể che giấu lỗ ngày/tuần, drawdown hoặc chuỗi lỗ. |

### Tạo và gộp lại giao dịch logic

1. Trong **Định hướng → Đánh giá**, chọn từ hai giao dịch logic một-vị-thế tương thích trở lên.
2. Chọn **Tạo giao dịch logic** và có thể thêm nhãn, ví dụ `London breakout scale-in`.
3. Lưu nhóm, sau đó mở giao dịch logic kết quả và hoàn tất một đánh giá sau giao dịch.

Chỉ có thể gộp vị thế khi chúng cùng tài khoản MT5, symbol, hướng và phiên bản chính sách rủi ro đã nhập. Nếu không nhập nhãn riêng, nhãn được tạo từ symbol, hướng và thời điểm vào đầu tiên. Nhóm trở thành một giao dịch logic có thể đánh giá và xuất hiện trong phân tích **Theo giao dịch** của dashboard khi vị thế thành viên **cuối cùng** đóng.

Thành viên và nhãn giao dịch logic có thể thay đổi. Dùng **Quản lý vị thế** trong bất kỳ đánh giá nào để thêm, bỏ, tách, gộp hoặc giải thể vị thế. Thay đổi thành viên không làm thay đổi dòng MT5; thay vào đó nó thay thế các đánh giá đã lưu bị ảnh hưởng, loại chúng khỏi điểm trụ cột và bằng chứng lộ trình đang hoạt động, đồng thời yêu cầu đánh giá mới. Đánh giá bị thay thế vẫn giữ ảnh chụp thành viên ban đầu và có trong lịch sử. Chỉ đổi nhãn thì không thay thế đánh giá.

### Báo cáo theo nhóm và rủi ro tự động

Một giao dịch logic được tính một lần trong số lượng giao dịch logic, tỷ lệ thắng, expectancy, tổng chiến lược và phân tích **Theo giao dịch**. Các phân tích đánh giá này tính lại theo cách gộp **hiện tại**. P&L ròng của nó là tổng P&L thành viên; ngày giao dịch logic là lúc thành viên cuối cùng đóng. Hãy mở chi tiết vị thế thành viên khi đánh giá để kiểm tra từng dòng MT5.

Số dư tài khoản, P&L thực hiện hằng ngày và drawdown luôn dùng các vị thế MT5 gốc theo thời gian. Vì vậy gộp lại không thể viết lại lịch sử tài khoản hoặc theo dõi giới hạn Rủi ro.

Với một nhóm, số tiền rủi ro tự động cộng các ước tính **SL thiết lập sẵn cụ thể** và **lỗ thực tế** theo từng vị thế. Phương án dự phòng **số dư trước giao dịch** khi được bật chỉ dùng số dư thực tế MT5 ghi lại ngay trước lần vào lệnh sớm nhất và áp dụng một lần cho giao dịch logic, không áp dụng cho từng vị thế thành viên. Nó chỉ mang tính tư vấn và thận trọng; không thay đổi SL thiếu trong MT5. Nếu MT5 không xác lập được số dư trước khi vào, việc tuân thủ chính sách chưa khả dụng cho đến khi người đánh giá nhập **Số tiền rủi ro thực tế** đã xác minh.

## 1. Thiết lập bằng chứng trước khi đánh giá

1. Thêm từng tài khoản MT5 trong **Cài đặt → Tài khoản và rủi ro** và nhập Funded capital khi biết.
2. Lưu **Chính sách rủi ro** tài khoản trong **Cài đặt → Tài khoản và rủi ro**:
   - Rủi ro chuẩn (1R) để báo cáo chuẩn hóa;
   - rủi ro tối đa mỗi giao dịch để kiểm tra tuân thủ;
   - giới hạn lỗ ngày/tuần, drawdown tối đa, chuỗi lỗ tối đa và R:R tối thiểu.
   - có thể bật **Dùng số dư MT5 trước giao dịch làm bằng chứng rủi ro tư vấn khi không có SL**. Tùy chọn này mặc định tắt và không bao giờ dùng Funded capital hoặc số dư hiện tại thay thế.
3. Tạo một hoặc nhiều **Chiến lược** trong **Cài đặt → Chiến lược**. Ghi quy tắc và bằng chứng backtest hiện có. Đánh giá đầy đủ cần một chiến lược được chọn để điểm Hệ thống có bằng chứng đánh giá.
4. Trong **Cài đặt → Quy tắc đánh giá**, chọn sự kiện nghiêm trọng nào là lỗi cứng cho đánh giá mới hoặc được sửa. Các cài đặt chỉ ảnh hưởng điểm và cảnh báo của nhật ký; không điều khiển MT5.

Chính sách rủi ro có phiên bản. Một đánh giá đã hoàn tất giữ lại chính sách và bằng chứng chiến lược được gắn lúc lưu. Sự kiện quy tắc cứng hiệu lực cũng được chụp lại, nên thay đổi cấu hình sau này không viết lại đánh giá cũ.

## 2. Điều gì được và không được chấm tự động

| Trạng thái đánh giá | Ý nghĩa | Điểm ba trụ cột? | Việc cần làm |
|---|---|---:|---|
| Cần xem xét | Bằng chứng rủi ro tự động vượt chính sách hoặc chưa khả dụng, và chưa được phê duyệt. | Không | Xem xét nhanh để phê duyệt bằng một nhấp chuột, hoặc hoàn tất đánh giá đầy đủ sau giao dịch. |
| Đã đánh giá tự động | Bằng chứng rủi ro tự động trong chính sách và vẫn đang chờ phê duyệt. | Không | Phê duyệt bằng một nhấp chuột, hoặc hoàn tất đánh giá đầy đủ. |
| Đã đánh giá | Bằng chứng tự động bạn đã phê duyệt (đánh dấu **Tự động**), hoặc đánh giá đầy đủ 13 tiêu chí (đánh dấu **Thủ công**). | Có | Không cần thêm gì với mục Tự động; sửa mục Thủ công bằng cách lưu đánh giá mới. |

Không bằng chứng rủi ro tự động nào tự nó được tính vào điểm số các trụ cột hay lộ trình sẵn sàng — kể cả khi nó trong chính sách. Nó phải được phê duyệt rõ ràng bằng một nhấp chuột (Xem xét nhanh hoặc Phê duyệt), hoặc được thay bằng đánh giá đầy đủ, trước đã. Sau khi phê duyệt, mục **Tự động** dùng `Một phần` (trung lập) cho Tâm lý và Hệ thống. Với Quản lý rủi ro, tuân thủ chính sách là `Đạt` khi số tiền tự động trong chính sách; các tiêu chí còn lại là trung lập. Phê duyệt bằng chứng vượt chính sách ghi tuân thủ chính sách là `Không đạt`. Đánh giá đầy đủ (**Thủ công**) vẫn là cách duy nhất để ghi vi phạm hoặc sự kiện quy tắc cứng, và luôn thay thế một phê duyệt hiện có.

### Bằng chứng rủi ro tự động

| Nguồn | Độ tin cậy | Diễn giải |
|---|---|---|
| SL thiết lập sẵn cụ thể | Đã xác minh | Rủi ro ban đầu do MT5 tính có trong tệp xuất. |
| Ước tính lỗ thực tế | Suy luận | `abs(P&L ròng)` của giao dịch lỗ không có rủi ro ban đầu đã tính. |
| Ước tính số dư trước giao dịch | Thận trọng | Số dư MT5 thực tế ngay trước khi vị thế có lãi không-SL mở, lấy từ sổ cái lệnh MT5. Chỉ có khi bật trong Chính sách rủi ro tài khoản. |

Ứng dụng so sánh số tiền khả dụng với chính sách rủi ro tối đa của tài khoản và gắn nhãn trong chính sách, vượt chính sách hoặc chưa khả dụng. Nhập **Số tiền rủi ro thực tế** đã xác minh khi số tiền tự động không phải bằng chứng tốt nhất. Nó thay thế số tiền tự động trong so sánh chính sách của giao dịch logic nhưng **không** viết lại chuỗi vị thế MT5 bất biến dùng cho giới hạn ngày/tuần, drawdown hay chuỗi lỗ.

### Theo dõi giới hạn tự động và đánh giá shutdown

Giới hạn lỗ ngày, lỗ tuần, drawdown và chuỗi lỗ được tính từ vị thế MT5 đã đóng. Khi một vị thế lần đầu chạm giới hạn, ứng dụng ghi cảnh báo **Đã chạm theo dõi rủi ro**. Vị thế đó không tự động là giao dịch thất bại: nhật ký không thể suy ra ý định trader hoặc điều đã biết khi lệnh còn mở.

Với vị thế vào sau thời điểm một vị thế hoàn tất trước đó đã chạm giới hạn, ứng dụng hiện ứng viên **Đánh giá shutdown**. Đây là lời nhắc kiểm tra chuỗi sự kiện, không phải kết luận. Chỉ chọn **Giao dịch sau shutdown cứng** khi đánh giá sau giao dịch xác nhận lệnh vào đã vi phạm quy tắc dừng của bạn và quy tắc cứng này được bật trong **Cài đặt → Quy tắc đánh giá** lúc lưu. Chỉ sự kiện đã xác nhận và được bật mới đổi trạng thái quy tắc cứng thành `KHÔNG ĐẠT`.

## 3. Hoàn tất một đánh giá sau giao dịch

Mở **Định hướng → Đánh giá** và chọn một giao dịch logic từ **Cần xem xét**, **Đã đánh giá tự động** hoặc **Đã đánh giá**. Bất kỳ bằng chứng rủi ro tự động nào cũng có thể được chấp nhận bằng một nhấp chuột, hoặc bạn có thể chấm đủ 13 tiêu chí. Một giao dịch logic đã gộp chỉ đóng góp một đánh giá vào mẫu trượt, không phải một đánh giá cho mỗi vị thế thành viên.

| Đánh giá | Giá trị số | Dùng khi |
|---|---:|---|
| Đạt | 100 | Tiêu chuẩn đã ghi chép được đáp ứng. |
| Một phần | 50 | Có lệch đáng kể, nhưng tiêu chí chưa thất bại hoàn toàn. |
| Không đạt | 0 | Tiêu chuẩn đã ghi chép không được đáp ứng. |

Đánh giá cần có ghi chú ngắn sau giao dịch. Thêm ít nhất một thẻ lý do khi bất kỳ tiêu chí nào **Không đạt**. Thêm một hành động cải thiện cụ thể khi bất kỳ tiêu chí nào **Một phần** hoặc **Không đạt**, hoặc khi có sự kiện quy tắc cứng. Điều này biến điểm thành cải thiện có thể kiểm chứng thay vì chỉ là nhãn.

### Tiêu chí Tâm lý — 35% / 25% / 20% / 20%

| Tiêu chí | Trọng số | Câu hỏi đánh giá |
|---|---:|---|
| Tuân thủ quy tắc | 35% | Tôi có theo quy tắc hành vi và thực hiện đã ghi chép không? |
| Kiểm soát bốc đồng | 25% | Tôi có tránh đuổi giá, vào lệnh vì chán và trả thù thị trường không? |
| Kiểm soát cảm xúc | 20% | Sợ hãi, tham lam, thất vọng hay FOMO có thay đổi quyết định không? |
| Kiên nhẫn và kỷ luật | 20% | Tôi có chờ cơ hội hợp lệ và thực hiện không ứng biến không? |

### Tiêu chí Quản lý rủi ro — 35% / 20% / 25% / 20%

| Tiêu chí | Trọng số | Câu hỏi đánh giá |
|---|---:|---|
| Tuân thủ chính sách | 35% | Giao dịch có phù hợp Chính sách rủi ro tài khoản không? |
| Độ chính xác khối lượng lệnh | 20% | Khối lượng có phù hợp rủi ro dự kiến không? |
| Kỷ luật Stop Loss | 25% | Stop/vô hiệu hóa có được tôn trọng thay vì nới rộng hoặc bỏ qua không? |
| Tuân thủ exposure và giới hạn rủi ro | 20% | Các kiểm soát phơi nhiễm áp dụng có được tôn trọng không? |

Rủi ro mở và kiểm soát tương quan được tự đánh giá vì cầu nối MT5 của giao dịch đóng không thể chứng minh tự động.

### Tiêu chí Hệ thống giao dịch — 30% / 20% / 20% / 15% / 15%

| Tiêu chí | Trọng số | Câu hỏi đánh giá |
|---|---:|---|
| Tính hợp lệ của setup | 30% | Setup chiến lược được chọn có thực sự hiện diện không? |
| Phù hợp bối cảnh | 20% | Thị trường, phiên, khung thời gian và regime có đáp ứng quy tắc chiến lược không? |
| Tuân thủ điểm vào lệnh | 20% | Điểm vào có theo trigger đã ghi chép không? |
| Tuân thủ điều kiện invalidation / Stop Loss | 15% | Logic vô hiệu hóa/stop có được áp dụng như đã ghi chép không? |
| Tuân thủ kế hoạch quản lý / thoát lệnh | 15% | Quản lý giao dịch và thoát lệnh có nhất quán với chiến lược không? |

## 4. Cách chấm một giao dịch

Mỗi trụ cột là tổng có trọng số của giá trị tiêu chí. **Điểm quy trình** thô là trung bình đơn giản của ba điểm trụ cột thô:

```text
Điểm trụ cột  = Σ(giá trị tiêu chí × trọng số tiêu chí)
Điểm quy trình = (Tâm lý + Quản lý rủi ro + Hệ thống giao dịch) / 3
```

Nhật ký chủ đích hiển thị hai kết quả riêng:

| Kết quả | Quy tắc |
|---|---|
| **Chất lượng giao dịch** | `Tốt` từ 70 trở lên; `Cần cải thiện` dưới 70. |
| **Trạng thái quy tắc cứng** | `Rõ ràng` trừ khi người đánh giá ghi sự kiện quy tắc cứng đã bật; `Không đạt` khi có. Sự kiện hiệu lực được chụp lúc lưu. |
| **Phân loại** | Lệnh thắng/Lệnh thua/Lệnh hòa vốn tốt hoặc cần cải thiện/xấu. Bất kỳ lỗi quy tắc cứng nào cũng là `Xấu`, bất kể điểm thô. |

Điều này ngăn điểm thô rất thấp được trình bày là giao dịch tốt chỉ vì không chọn quy tắc cứng.

### Ví dụ: giao dịch tốt có một lệch hành vi

Giả sử mọi tiêu chí là **Đạt**, trừ **Tuân thủ quy tắc** của Tâm lý là **Một phần**.

```text
Tâm lý = (50 × 35%) + (100 × 25%) + (100 × 20%) + (100 × 20%)
        = 17.5 + 25 + 20 + 20
        = 82.5

Quản lý rủi ro = 100
Hệ thống giao dịch = 100

Điểm quy trình thô = (82.5 + 100 + 100) / 3
                    = 94.17
```

Giao dịch này có chất lượng **Tốt** và trạng thái quy tắc cứng **Rõ ràng** nếu không có sự kiện quy tắc cứng. P&L sau đó quyết định nó là Lệnh thắng tốt, Lệnh thua tốt hay Lệnh hòa vốn tốt. Điểm 94.17 không có nghĩa bỏ qua mức Một phần: hành động cải thiện và thẻ vẫn có để phân tích mẫu hình.

### Ví dụ: tại sao trung bình cao không thể che vi phạm nghiêm trọng

Giả sử vẫn điểm thô 94.17 nhưng trader ghi sự kiện quy tắc cứng **Cố ý nới rộng stop** đã bật.

```text
Điểm quy trình thô = 94.17     (giữ làm bằng chứng)
Trạng thái quy tắc cứng = KHÔNG ĐẠT (quy tắc cứng ghi đè phân loại)
Phân loại = Lệnh thắng xấu / Lệnh thua xấu / Lệnh hòa vốn xấu
Trụ cột Rủi ro = bị chặn cứng trong mẫu trượt
```

Nếu giao dịch có lãi, đó là **Lệnh thắng xấu**. Nếu lỗ, đó là **Lệnh thua xấu**. Điểm thô vẫn hiển thị để đánh giá có thể kiểm toán; nó không hủy lỗi cứng.

## 5. Quy tắc cứng và vi phạm nghiêm trọng

Các sự kiện sau có thể bật làm lỗi cứng trong **Cài đặt → Quy tắc đánh giá**:

| Sự kiện | Trụ cột bị ảnh hưởng khi bật | Ý nghĩa |
|---|---|---|
| Giao dịch trả thù quá khổ | Tâm lý và Quản lý rủi ro | Tăng khối lượng do cảm xúc hoặc trả thù. |
| Thiếu setup bắt buộc | Hệ thống giao dịch | Giao dịch được thực hiện khi thiếu setup yêu cầu. |
| Cố ý nới rộng stop | Quản lý rủi ro | Rủi ro bị tăng do dời stop xa hơn. |
| Giao dịch sau shutdown cứng | Quản lý rủi ro | Giao dịch được thực hiện sau điều kiện dừng đã cấu hình. |

Quy tắc cứng thực hiện ba việc:

1. Đặt **Trạng thái quy tắc cứng** của giao dịch là `KHÔNG ĐẠT` và phân loại là `Xấu`.
2. Đánh dấu trụ cột bị ảnh hưởng là bị chặn cứng khi giao dịch đã đánh giá còn nằm trong cửa sổ trượt.
3. Ngăn mức sẵn sàng báo `Sẵn sàng`, dù điểm số cao.

Thẻ lý do cũng làm mẫu hình lặp lại hiển thị. Thẻ nghiêm trọng Tâm lý gồm trả thù, tăng khối lượng cảm xúc và không reset sau lỗ; Rủi ro gồm vi phạm ngày/tuần/drawdown/phơi nhiễm và nới stop; thẻ nghiêm trọng Hệ thống là thiếu setup bắt buộc. Một thẻ không tự thành quy tắc cứng trừ khi cài đặt liên quan được bật lúc đánh giá được lưu. Đặc biệt, cảnh báo giới hạn MT5 tự động và ứng viên đánh giá shutdown không tạo lỗi cứng nếu người đánh giá không ghi sự kiện **Giao dịch sau shutdown cứng** đã bật. Thay đổi Quy tắc đánh giá về sau chỉ áp dụng cho đánh giá mới hoặc sửa, không sửa phân loại lịch sử.

## 6. Cách tính theo dõi trượt

Trong **Định hướng → Theo dõi**, chọn cửa sổ trượt từ 10 đến 100 giao dịch (thanh trượt, bước 5; ô xem gọn trên Dashboard luôn hiển thị cửa sổ cố định 20 giao dịch). Đánh giá tự động, đánh giá tự động đã phê duyệt và giao dịch đánh giá thủ công đều vào cửa sổ. Bản nhập cần phê duyệt nằm ngoài đến khi được phê duyệt hoặc đánh giá đầy đủ. Cửa sổ nhỏ hơn sẽ đạt ngưỡng Cảnh báo do vi phạm nghiêm trọng lặp lại sớm hơn cửa sổ lớn hơn, vì ngưỡng này là một số đếm cố định, không phải tỷ lệ phần trăm của cửa sổ.

Theo dõi tính một bộ thành phần giai đoạn thứ hai từ cửa sổ đã đánh giá. Chúng không phải trung bình đơn giản của điểm trụ cột mỗi giao dịch nhìn thấy; chúng được thiết kế để chỉ ra hành vi lặp lại và chất lượng bằng chứng.

### Điểm theo dõi Tâm lý

| Thành phần | Trọng số | Cách đo |
|---|---:|---|
| Tuân thủ quy tắc | 35% | Trung bình mức Tuân thủ quy tắc đã đánh giá. |
| Kiểm soát bốc đồng | 25% | Trung bình mức Kiểm soát bốc đồng đã đánh giá. |
| Kiểm soát cảm xúc | 20% | Trung bình mức Kiểm soát cảm xúc đã đánh giá. |
| Kỷ luật sau lỗ | 20% | Giao dịch đã đánh giá tiếp theo sau một lỗ trên mọi tài khoản: mức Kiểm soát bốc đồng của nó, hoặc 0 khi gắn thẻ `post_loss_reset`. Là 100 khi mẫu không có chuỗi sau-lỗ đủ điều kiện. |

### Điểm theo dõi Quản lý rủi ro

| Thành phần | Trọng số | Cách đo |
|---|---:|---|
| Tuân thủ chính sách | 35% | Trung bình mức Tuân thủ chính sách đã đánh giá. |
| Kỷ luật Stop Loss | 25% | Trung bình mức Kỷ luật Stop Loss đã đánh giá. |
| Tuân thủ giới hạn | 25% | 100 cho giao dịch đã đánh giá không có sự kiện ngày/tuần/drawdown/chuỗi lỗ lịch sử; 0 khi có sự kiện. Chỉ ảnh hưởng thành phần theo dõi Rủi ro; không tự đặt trạng thái quy tắc cứng `KHÔNG ĐẠT`. |
| Kiểm soát phơi nhiễm | 15% | Trung bình mức Tuân thủ exposure và giới hạn rủi ro đã đánh giá. |

### Điểm theo dõi Hệ thống giao dịch

| Thành phần | Trọng số | Cách đo |
|---|---:|---|
| Tính hợp lệ của setup | 20% | Trung bình mức Tính hợp lệ của setup đã đánh giá. |
| Tuân thủ kế hoạch execution | 20% | Trung bình mức Điểm vào, Vô hiệu hóa và Quản lý/thoát lệnh. |
| Phù hợp bối cảnh | 15% | Trung bình mức Phù hợp bối cảnh đã đánh giá. |
| Chất lượng bằng chứng | 20% | 100 khi chiến lược gắn có mô tả, ngày backtest và ít nhất 100 giao dịch backtest; 50 khi đã ghi chép nhưng dưới 100; nếu không là 0. |
| Bằng chứng edge | 25% | 100 cho ít nhất 100 giao dịch backtest có expectancy dương sau chi phí; 50 cho ít nhất 50 giao dịch expectancy dương; nếu không là 0. |

### Trạng thái, mức độ đầy đủ và mức sẵn sàng

- Một trụ cột còn **Chưa đủ** đến khi cửa sổ đã chọn đầy, dù ứng dụng có thể hiện điểm số sớm từ các đánh giá đã có.
- Một trụ cột ở **Cảnh báo** khi số vi phạm nghiêm trọng lặp lại đạt ngưỡng cấu hình. Điểm số bị giới hạn ở **59** cho đến khi lưu đánh giá tuần hoặc tháng sau đó.
- Một trụ cột **Không đạt** khi có lỗi quy tắc cứng đang hoạt động trong cửa sổ đã chọn. Lỗi cứng ưu tiên hơn cảnh báo và điểm số.
- **Mức sẵn sàng** là điểm *thấp nhất* của trụ cột đầy đủ, không phải trung bình. Nó chưa đủ cho đến khi cả ba trụ cột có cửa sổ đầy và bằng chứng đo lường được. Nó là `KHÔNG ĐẠT` khi bất kỳ trụ cột nào có chặn cứng đang hoạt động.
- Điểm dưới **70** tạo cảnh báo trụ cột đang phát triển. Cảnh báo/thanh dừng Rủi ro, chặn cứng đang hoạt động và đánh giá giai đoạn quá hạn tạo cảnh báo hồi cứu.

## 7. Dùng đánh giá tuần hoặc tháng để cải thiện một việc

Khi đến hạn đánh giá tuần hoặc tháng, hãy lưu:

- phản hồi ngắn gọn về giai đoạn đã hoàn tất;
- các thẻ lặp lại hoặc điểm yếu đang xử lý; và
- **một** hành động cải thiện ưu tiên cho giai đoạn tiếp theo.

Đánh giá đã lưu chụp lại điểm, cảnh báo, vấn đề lặp lại và hành động của giai đoạn hoàn tất. Bản nhập sau này không viết lại giai đoạn đã lưu. Một đánh giá lưu sau vi phạm nghiêm trọng lặp lại gần nhất sẽ bỏ giới hạn cảnh báo 59 điểm; nó không xóa chặn cứng đang hoạt động hoặc thay đổi bằng chứng giao dịch lịch sử.

Dùng chẩn đoán thay vì P&L gần đây để chọn hành động:

| Mẫu hình | Diễn giải | Hành động ví dụ |
|---|---|---|
| Hệ thống mạnh, Tâm lý yếu | Edge có thể vẫn còn; thực hiện là vấn đề. | Thêm quy tắc dừng sau lỗ và đánh giá nó trong 20 lần xem xét tiếp theo. |
| Tâm lý và Hệ thống mạnh, Rủi ro yếu | Chất lượng quyết định ổn nhưng bảo vệ vốn chưa tốt. | Luyện tính khối lượng và yêu cầu ghi số tiền rủi ro cho 10 giao dịch tiếp theo. |
| Tâm lý và Rủi ro mạnh, Hệ thống yếu | Quy trình có kỷ luật nhưng setup/bằng chứng cần cải thiện. | Đóng băng quy tắc chiến lược và thu thập hoặc xác minh thêm backtest trước khi đổi cách thực hiện. |

Không đổi chiến lược chỉ vì mẫu P&L gần đây nhỏ. Hãy lập một giả thuyết, thu thập bằng chứng, rồi giữ hoặc loại thay đổi.

### Trọng tâm huấn luyện

Theo dõi chỉ giữ một **Trọng tâm huấn luyện** cho toàn nhật ký. Trade Compass tự chọn trọng tâm từ bằng chứng đã đánh giá, rồi theo dõi 5, 10 hoặc 20 giao dịch mới đã đánh giá trước khi bạn ghi phản hồi kết thúc. Tâm lý dùng bằng chứng trên toàn trader; Quản lý rủi ro và Hệ thống giao dịch dùng bằng chứng của tài khoản được chọn, vì mỗi tài khoản đại diện cho một hệ thống độc lập. Ứng dụng vẫn chỉ tư vấn sau giao dịch.

## 8. Lộ trình cải thiện và các cổng

Ba trụ cột tiến song song:

| Mức | Cổng |
|---|---|
| Xác định | Quy tắc và bằng chứng đã được ghi chép. |
| Kiểm chứng | Bằng chứng kiểm thử hoặc thực hành đã được ghi chép. Kiểm thử Hệ thống cần hơn 100 giao dịch backtest có expectancy dương sau chi phí. |
| Thực hiện | 20 đánh giá đầy đủ, điểm ít nhất 70 và không có lỗi cứng đang hoạt động. |
| Đo lường | 30 đánh giá đầy đủ, một đánh giá tuần hoặc tháng đã lưu cho giai đoạn hoàn tất gần nhất, điểm 30 đánh giá ít nhất 80 và không có lỗi cứng. |
| Tối ưu | Một giả thuyết, đường cơ sở, kết quả và quyết định giữ/loại đã được ghi lại. |

Lộ trình sẵn sàng tiến song song ở cả ba trụ cột. Bằng chứng **Tâm lý** tập trung vào hành vi; **Quản lý rủi ro** là bằng chứng chính sách và khối lượng theo tài khoản; **Hệ thống giao dịch** là quy tắc chiến lược, ví dụ và bằng chứng backtest.

Hầu hết các mục trong lộ trình được tự động phát hiện từ dữ liệu đã lưu sẵn ở nơi khác trong nhật ký, không cần thao tác thủ công:

- Thực hiện và Đo lường (cả ba trụ cột) tự hoàn tất ngay khi đủ điều kiện về số lượng đánh giá/điểm số/lỗi cứng/đánh giá giai đoạn.
- Tối ưu (cả ba trụ cột) tự hoàn tất khi một Trọng tâm coaching của trụ cột đó đã được giải quyết (hoàn tất hoặc từ bỏ) kèm ghi chú phản ánh.
- Bước Xác định của Rủi ro tự hoàn tất ngay khi chính sách rủi ro của tài khoản được lưu.
- Các bước Xác định, Kiểm chứng và backtest của Hệ thống giao dịch tự hoàn tất khi hồ sơ chiến lược có mô tả, một setup có ví dụ minh họa, và bằng chứng backtest (100+ giao dịch, expectancy dương) tương ứng.

Chỉ những mục không có dữ liệu tương ứng nào trong ứng dụng mới cần tự xác nhận thủ công — bước Xác định và Kiểm chứng của Tâm lý, và bước Kiểm chứng "bằng chứng tính toán/mô phỏng rủi ro" của Rủi ro. Chỉ hoàn tất các mục đó trong **Định hướng → Cải thiện** khi có thể giải thích và xem lại.

## 9. Giới hạn dữ liệu

Cầu nối MT5 hiện tại chỉ cung cấp vị thế đã đóng. Nó có thể theo dõi hồi cứu R thực hiện, giới hạn ngày/tuần, drawdown, chuỗi lỗ, thông tin entry-stop đã xuất và ảnh chụp số dư tài khoản. Nó không thể chứng minh rủi ro mở lịch sử, phơi nhiễm tương quan, mọi lần chỉnh stop trong giao dịch, trạng thái tinh thần, ý định kế hoạch hay stop ban đầu thực cho bản xuất có lãi không-SL. Những giới hạn này là lý do nhật ký kết hợp bằng chứng tự động với đánh giá sau giao dịch có chủ đích của con người.
