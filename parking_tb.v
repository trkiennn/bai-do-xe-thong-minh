`timescale 1ns/1ps
module parking_tb;
 reg [14:0] parking_sensor;
 reg [14:0] visual_sensor;
 wire [4:0] occupied_count, empty_count;
 wire parking_full, allow_entry;
 integer fd, read_ok;
 parking_system uut(
   .parking_sensor(parking_sensor),
   .occupied_count(occupied_count),
   .empty_count(empty_count),
   .parking_full(parking_full),
   .allow_entry(allow_entry)
 );
 initial begin
   parking_sensor=15'b0; visual_sensor=15'b0;
   fd=$fopen("parking_input.txt","r");
   if(fd==0) begin $display("ERROR: Khong mo duoc parking_input.txt"); $finish; end
   read_ok=$fscanf(fd,"%b",visual_sensor);
   $fclose(fd);
   if(read_ok!=1) begin $display("ERROR: Sensor khong hop le"); $finish; end
   parking_sensor=visual_sensor;
   #1 $display("RESULT %0d %0d %0d %0d",occupied_count,empty_count,parking_full,allow_entry);
   #1 $finish;
 end
endmodule
