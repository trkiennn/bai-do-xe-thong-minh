`timescale 1ns/1ps
module parking_system(
 input wire [14:0] parking_sensor,
 output reg [4:0] occupied_count,
 output reg [4:0] empty_count,
 output reg parking_full,
 output reg allow_entry
);
 integer i; integer count;
 always @(*) begin
   count=0;
   for(i=0;i<15;i=i+1)
     if(parking_sensor[i]) count=count+1;
   occupied_count=count;
   empty_count=15-count;
   parking_full=(count>=15);
   allow_entry=(count<15);
 end
endmodule
