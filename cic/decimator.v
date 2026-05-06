module decimator (
    i_clk,
    i_data,
    i_ready,
    o_data,
    o_ready
    );

    parameter W=5, R=5;

    input wire i_clk;
    input wire i_ready;
    input wire signed [(W-1):0] i_data;
    output reg signed [(W-1):0] o_data;
    output reg o_ready = 1'b0;

    // Counter runs from 0 to R-1, then resets.
    // Old code: counter incremented THEN tested, causing double-fire at rollover.
    // Fix: reset counter before increment so it never reaches R.
    reg [31:0] i_ready_counter = 32'b0;

    always @(posedge i_clk) begin
        o_ready <= 1'b0;                    // default: no output this cycle
        if (i_ready) begin
            if (i_ready_counter == (R - 1)) begin
                i_ready_counter <= 32'b0;
                o_data          <= i_data;
                o_ready         <= 1'b1;
            end else begin
                i_ready_counter <= i_ready_counter + 1'b1;
            end
        end
    end

endmodule
