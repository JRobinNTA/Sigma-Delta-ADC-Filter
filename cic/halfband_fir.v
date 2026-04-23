module halfband_fir #(
    parameter W = 32
)(
    input  wire               clk,
    input  wire               rst,
    input  wire               i_valid,
    input  wire signed [W-1:0] i_data,
    output reg                o_valid,
    output reg  signed [W-1:0] o_data
);

    // Only need outer coefficients due to symmetry and zeros
    // h0, 0, h2, 0.5, h2, 0, h0
    localparam signed [15:0] H0 = -16'h003A;
    localparam signed [15:0] H2 = 16'h02A4;

    reg signed [W-1:0] delay_line [0:6];
    reg decimation_toggle;
    integer i;

    // Structural Optimization: Pre-add symmetric taps before multiplying
    wire signed [W:0] sum_h0 = delay_line[0] + delay_line[6];
    wire signed [W:0] sum_h2 = delay_line[2] + delay_line[4];

    // ... (Keep your delay lines and sum_h0 / sum_h2 exactly the same) ...

    // The center tap is a cheap arithmetic shift right (multiply by 0.5)
    wire signed [W-1:0] center_tap = delay_line[3] >>> 1;

    // 1. MAC (Multiply-Accumulate) generates a 49-bit result (33 bit sum * 16 coeff)
    wire signed [48:0] mac_sum = (sum_h0 * H0) + (sum_h2 * H2);

    // 2. Shift the MAC result
    wire signed [48:0] shifted_mac = mac_sum >>> 15;

    // 3. Truncate back to W (32 bits) BEFORE the final addition
    wire signed [W-1:0] truncated_mac = shifted_mac[W-1:0];

    // 4. Final addition (W+1 bits to catch overflow)
    wire signed [W:0] final_output_extended = truncated_mac + center_tap;

    always @(posedge clk) begin
        if (rst) begin
            for (i=0; i<7; i=i+1) delay_line[i] <= 0;
            decimation_toggle <= 1'b0;
            o_valid <= 1'b0;
            o_data <= 0;
        end else if (i_valid) begin
            // Shift register
            delay_line[0] <= i_data;
            for (i=1; i<7; i=i+1) delay_line[i] <= delay_line[i-1];

            // Toggle to drop every other sample (Decimate by 2)
            decimation_toggle <= ~decimation_toggle;

            if (decimation_toggle) begin
                // Explicitly slice the final W bits to satisfy Verilator
                o_data <= final_output_extended[W-1:0];
                o_valid <= 1'b1;
            end else begin
                o_valid <= 1'b0;
            end
        end else begin
            o_valid <= 1'b0;
        end
    end

endmodule
