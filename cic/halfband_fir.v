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

    // -----------------------------------------------------------------------
    // 7-tap halfband FIR: h = [H0, 0, H2, 0.5, H2, 0, H0]
    // Q15 formatting: divide by 32768 (>> 15)
    // -----------------------------------------------------------------------
    // Bulletproof coefficients (32-bit signed integers, avoids 16-bit sign-ext traps)
    localparam signed [31:0] H0     = -2048;   // -0.0625 * 32768
    localparam signed [31:0] H2     =  10240;  //  0.3125 * 32768
    localparam signed [31:0] H_CENT =  16384;  //  0.5000 * 32768

    // Delay line
    reg signed [W-1:0]   delay_line [0:6];
    reg                  decimation_toggle;
    integer              k;

    // Use a massive 64-bit internal accumulator
    localparam ACC_W = 64;

    /* verilator lint_off WIDTHEXPAND */
    wire signed [ACC_W-1:0] d0 = $signed(delay_line[0]);
    wire signed [ACC_W-1:0] d2 = $signed(delay_line[2]);
    wire signed [ACC_W-1:0] d3 = $signed(delay_line[3]);
    wire signed [ACC_W-1:0] d4 = $signed(delay_line[4]);
    wire signed [ACC_W-1:0] d6 = $signed(delay_line[6]);
    /* verilator lint_on WIDTHEXPAND */

    // Uniform multiply-accumulate (all operands explicitly signed and safe)
    wire signed [ACC_W-1:0] acc = 
        (d0 + d6) * H0 + 
        (d2 + d4) * H2 + 
        (d3 * H_CENT);

    // Q15 normalise with round-to-nearest (+ 0.5 LSB before shift)
    wire signed [ACC_W-1:0] acc_rounded = acc + 16384;
    wire signed [ACC_W-1:0] acc_shifted = acc_rounded >>> 15;

    // Hardcoded safe saturation bounds for W=32 (avoids 1 <<< 31 sign bugs entirely)
    wire signed [ACC_W-1:0] max_val =  64'sd2147483647;
    wire signed [ACC_W-1:0] min_val = -64'sd2147483648;

    wire signed [W-1:0] saturated = 
        (acc_shifted > max_val) ? 32'sd2147483647 :
        (acc_shifted < min_val) ? -32'sd2147483648 :
        acc_shifted[W-1:0];

    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (rst) begin
            for (k = 0; k < 7; k = k+1) delay_line[k] <= 0;
            decimation_toggle <= 1'b0;
            o_valid           <= 1'b0;
            o_data            <= 0;
        end else if (i_valid) begin
            // Shift register
            delay_line[0] <= i_data;
            for (k = 1; k < 7; k = k+1)
                delay_line[k] <= delay_line[k-1];

            // Decimate by 2
            decimation_toggle <= ~decimation_toggle;
            if (decimation_toggle) begin
                o_data  <= saturated;
                o_valid <= 1'b1;
            end else begin
                o_valid <= 1'b0;
            end
        end else begin
            o_valid <= 1'b0;
        end
    end

endmodule
